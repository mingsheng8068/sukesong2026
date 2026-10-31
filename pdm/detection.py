"""固定处理流水线的前两步：异常检测 → 归并成事件（纯代码，不调模型）。

两类事件：
- 持续偏离：振动 6 小时均值相对“之前最近 168 个未告警小时”的基线偏高，同一台设备相隔不超过 12 小时的告警归成一个事件；
- 疑似传感器尖峰：温度或电流单小时突跳，连续不超过 2 小时，且当时振动没有告警；要等之后第一个有效小时回到正常才确认。
  它是待判别对象，不直接算误报。

转速、负载率、湿度在官方数据里像纯噪声（夜间停机时转速也不降），不参与检测。

每个小时的判断只用这个小时及之前的数据，所以把数据截到任意 as_of 重新算，结果等于当时实时算出来的。
参数是在官方 16 次故障上调定的（DECISIONS D23），换到真实设备要重新标定。

    python -m pdm.detection        # 打印事件表和统计
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property, lru_cache

import numpy as np
import pandas as pd

from .adapters import load_sensors, short_code

WINDOW_H = 6              # 振动滚动均值的窗口
MIN_WINDOW_POINTS = 4     # 窗口里至少几个有效值（单个缺失值直接跳过，不插值）
BASELINE_H = 168          # 基线：窗口之前的 7 天
MIN_BASELINE_POINTS = 72  # 基线至少 3 天有效值，不够就不判
Z_ALARM = 2.0             # 振动告警阈值
MERGE_GAP_H = 12          # 相隔不超过 12 小时的告警算同一次上升
SPIKE_SIGNALS = ("temp", "current")
Z_SPIKE = 4.0             # 尖峰阈值：4σ 时偶然超出的期望不到 1 次（3σ 时约 15 次）
MAX_SPIKE_H = 2           # 连续超过 2 小时就不算“孤立”尖峰
MIN_RELATIVE_STD = 1e-6   # 标准差不到均值的百万分之一，视为基线没有波动（常量算出的 4e-16 这类浮点误差）

SUSTAINED = "持续偏离"
SPIKE = "疑似传感器尖峰"


EVENT_COLUMNS = ["id", "kind", "equipment_id", "signals", "start", "end", "alarm_hours", "peak_z", "peak_at",
                 "visible_from"]
SPIKE_PART_COLUMNS = ["id", "signal", "equipment_id", "start", "end", "alarm_hours", "peak_z", "peak_at",
                      "visible_from"]
HOURLY_COLUMNS = ["time", "equipment_id", "vib_mean6", "vib_z", "vib_alarm", *(f"{s}_z" for s in SPIKE_SIGNALS)]


@dataclass(frozen=True)
class Detection:
    hourly: pd.DataFrame       # 逐小时：time, equipment_id, vib_mean6, vib_z, vib_alarm, temp_z, current_z
    sustained: pd.DataFrame    # 持续偏离事件（全量数据）
    spike_parts: pd.DataFrame  # 每个信号各自确认的尖峰，各有各的可见时间；按 as_of 再合成事件

    @cached_property
    def events(self) -> pd.DataFrame:
        """全部数据看完之后的事件表（一行一个事件）。"""
        return events_as_of(self, pd.Timestamp.max)


def event_id(equipment_id: str, start: pd.Timestamp) -> str:
    return f"EV-{short_code(equipment_id)}-{start:%m%d}T{start:%H}"


def has_spread(std, mean):
    """基线有没有可用的波动。没有就算不出 z，记为不可判定（NaN），不告警。检测和工具共用这一条。

    常量基线（比如全是 1.1）算出来的标准差不是 0 而是 4e-16，直接拿来除会得到 10^15 级的 z，
    所以按相对大小判断，不用“std > 0”。标量和 pandas Series 都能传。
    """
    return std > MIN_RELATIVE_STD * np.maximum(np.abs(mean), 1.0)


def vibration_alarms(vib: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回每小时的 6h 均值、z 和是否告警。

    基线取 6h 窗口之前最近的 168 个“未告警、不缺失”的小时（冻结基线）：故障期的高值不会抬高基线，
    持续告警超过 7 天时也还沿用告警前的基线，不会把异常“学”成正常。
    """
    values = vib.to_numpy(dtype=float)
    mean6 = vib.rolling(WINDOW_H, min_periods=MIN_WINDOW_POINTS).mean().to_numpy()
    z = np.full(len(values), np.nan)
    alarm = np.zeros(len(values), dtype=bool)
    usable = ~np.isnan(values)
    for i in range(len(values)):
        end = i - WINDOW_H + 1                # 基线截止到 6h 窗口开始之前
        if end <= 0 or np.isnan(mean6[i]):
            continue
        base = values[:end][usable[:end] & ~alarm[:end]][-BASELINE_H:]
        if len(base) < MIN_BASELINE_POINTS:
            continue
        mean, std = base.mean(), base.std(ddof=1)
        if not has_spread(std, mean):         # 基线没有波动：z 无意义，记为不可判定（NaN），不告警
            continue
        z[i] = (mean6[i] - mean) / std
        alarm[i] = z[i] > Z_ALARM
    return mean6, z, alarm


def spike_z(values: pd.Series) -> pd.Series:
    """单小时值相对之前 168 小时的偏离（不含当前小时）。基线没有波动时为 NaN（不可判定）。"""
    previous = values.shift(1).rolling(BASELINE_H, min_periods=MIN_BASELINE_POINTS)
    mean, std = previous.mean(), previous.std()
    return (values - mean) / std.where(has_spread(std, mean))


def _runs(flags: np.ndarray, gap: int) -> list[tuple[int, int]]:
    """把为 True 的小时连成段；两段之间空出的小时数不超过 gap 就并成一段。"""
    runs: list[list[int]] = []
    for i in np.flatnonzero(flags):
        if runs and i - runs[-1][1] - 1 <= gap:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(a, b) for a, b in runs]


def _confirmed_spikes(z: pd.Series, vib_alarm: np.ndarray) -> list[tuple[int, int, int]]:
    """单个信号里已确认的孤立尖峰，返回 (开始, 结束, 确认恢复的那一小时) 的下标。

    确认要等到尖峰之后第一个有效小时回到正常：下一小时缺失不算恢复，再往后看；
    尖峰之前最近的有效小时也必须正常，否则它只是一段更长异常的尾巴。
    """
    values = z.to_numpy(dtype=float)
    valid = ~np.isnan(values)
    abnormal = valid & (np.abs(values) > Z_SPIKE)
    confirmed = []
    for a, b in _runs(abnormal & ~vib_alarm, gap=0):
        before = np.flatnonzero(valid[:a])
        after = b + 1 + np.flatnonzero(valid[b + 1:])
        if before.size and abnormal[before[-1]]:
            continue
        if not after.size or abnormal[after[0]]:     # 还没恢复，或者恢复之前数据就断了
            continue
        if after[0] - a > MAX_SPIKE_H:               # 连同中间缺失的小时，超过 2 小时不算孤立
            continue
        confirmed.append((a, b, int(after[0])))
    return confirmed


def _detect_device(table: pd.DataFrame) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    table = table.reset_index(drop=True)
    equipment_id = table["equipment_id"].iloc[0]
    times = table["time"]
    mean6, vib_z, vib_alarm = vibration_alarms(table["vib"])
    hourly = pd.DataFrame({
        "time": times, "equipment_id": equipment_id,
        "vib_mean6": mean6, "vib_z": vib_z, "vib_alarm": vib_alarm,
    })

    sustained = []
    for a, b in _runs(vib_alarm, MERGE_GAP_H):
        inside = np.arange(a, b + 1)[vib_alarm[a:b + 1]]
        peak = inside[np.nanargmax(vib_z[inside])]
        sustained.append({
            "id": event_id(equipment_id, times[a]), "kind": SUSTAINED, "equipment_id": equipment_id,
            "signals": ("vib",), "start": times[a], "end": times[b], "alarm_hours": len(inside),
            "peak_z": round(float(vib_z[peak]), 2), "peak_at": times[peak], "visible_from": times[a],
        })

    spike_parts = []
    for signal in SPIKE_SIGNALS:
        z = spike_z(table[signal])
        hourly[f"{signal}_z"] = z
        for a, b, recovered in _confirmed_spikes(z, vib_alarm):
            peak = a + int(np.argmax(z.iloc[a:b + 1].abs().to_numpy()))
            spike_parts.append({
                "id": event_id(equipment_id, times[a]), "signal": signal, "equipment_id": equipment_id,
                "start": times[a], "end": times[b], "alarm_hours": b - a + 1,
                "peak_z": round(float(z.iloc[peak]), 2), "peak_at": times[peak],
                "visible_from": times[recovered],    # 回到正常的那一小时，才知道它是孤立的
            })
    return hourly, sustained, spike_parts


def detect(sensors: pd.DataFrame) -> Detection:
    hourly_parts, sustained, spike_parts = [], [], []
    for _, table in sensors.groupby("equipment_id", sort=True):
        hourly, device_sustained, device_spikes = _detect_device(table)
        hourly_parts.append(hourly)
        sustained.extend(device_sustained)
        spike_parts.extend(device_spikes)
    hourly = pd.concat(hourly_parts, ignore_index=True) if hourly_parts else pd.DataFrame(columns=HOURLY_COLUMNS)
    return Detection(hourly, pd.DataFrame(sustained, columns=EVENT_COLUMNS),
                     pd.DataFrame(spike_parts, columns=SPIKE_PART_COLUMNS))


@lru_cache(maxsize=1)
def official_detection() -> Detection:
    return detect(load_sensors())


def events_as_of(detection: Detection, as_of: pd.Timestamp) -> pd.DataFrame:
    """站在 as_of 能看到的事件。

    持续事件：开始 ≤ as_of，结束、告警小时数、峰值只算到 as_of；
    尖峰：只用 as_of 时已经确认的那些信号，按（设备、开始小时）合成一个事件，温度和电流同一小时都跳算一个。
    """
    rows = []
    hourly = detection.hourly
    for event in detection.sustained[detection.sustained["start"] <= as_of].to_dict("records"):
        seen = hourly[(hourly["equipment_id"] == event["equipment_id"]) & hourly["vib_alarm"]
                      & (hourly["time"] >= event["start"]) & (hourly["time"] <= min(event["end"], as_of))]
        peak = seen.loc[seen["vib_z"].idxmax()]
        event.update(end=seen["time"].iloc[-1], alarm_hours=len(seen),
                     peak_z=round(float(peak["vib_z"]), 2), peak_at=peak["time"])
        rows.append(event)

    parts = detection.spike_parts[detection.spike_parts["visible_from"] <= as_of]
    for (equipment_id, start), group in parts.groupby(["equipment_id", "start"], sort=False):
        peak = group.loc[group["peak_z"].abs().idxmax()]
        rows.append({
            "id": group["id"].iloc[0], "kind": SPIKE, "equipment_id": equipment_id,
            "signals": tuple(s for s in SPIKE_SIGNALS if s in set(group["signal"])),
            "start": start, "end": group["end"].max(), "alarm_hours": int(group["alarm_hours"].max()),
            "peak_z": float(peak["peak_z"]), "peak_at": peak["peak_at"], "visible_from": group["visible_from"].min(),
        })
    table = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    return table.sort_values(["start", "equipment_id"], kind="stable").reset_index(drop=True)


OPEN_AFTER_ALARM_H = 6   # 默认在累计告警 6 小时时点开事件


def default_as_of(detection: Detection, event: dict) -> pd.Timestamp:
    """默认调查时点：持续事件在累计告警满 6 小时那一刻被点开；尖峰在确认孤立的那一刻。"""
    if event["kind"] != SUSTAINED:
        return event["visible_from"]
    hourly = detection.hourly
    alarms = hourly[(hourly["equipment_id"] == event["equipment_id"]) & hourly["vib_alarm"]
                    & hourly["time"].between(event["start"], event["end"])]["time"]
    return alarms.iloc[min(OPEN_AFTER_ALARM_H, len(alarms)) - 1]


def main() -> None:
    detection = official_detection()
    events = detection.events
    sustained = events[events["kind"] == SUSTAINED]
    spikes = events[events["kind"] == SPIKE]
    alarm_hours = int(detection.hourly["vib_alarm"].sum()) + int(spikes["alarm_hours"].sum())
    print(f"告警 {alarm_hours} 小时（振动 {int(detection.hourly['vib_alarm'].sum())} + 尖峰 {int(spikes['alarm_hours'].sum())}）"
          f" → 事件 {len(events)} 个（持续偏离 {len(sustained)}，疑似传感器尖峰 {len(spikes)}）\n")
    view = events.assign(
        signals=events["signals"].map("+".join),
        hours=((events["end"] - events["start"]) / pd.Timedelta(hours=1)).astype(int) + 1,
        start=events["start"].dt.strftime("%m-%d %H:%M"),
        end=events["end"].dt.strftime("%m-%d %H:%M"),
        default_as_of=[f"{default_as_of(detection, e):%m-%d %H:%M}" for e in events.to_dict("records")],
    )
    print(view[["id", "kind", "signals", "start", "end", "hours", "alarm_hours", "peak_z", "default_as_of"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
