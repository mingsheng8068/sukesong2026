"""数据工具：agent 查证据用。每个函数只返回调查时点 as_of 之前就已经能知道的信息。

as_of 由程序传入（agent/toolbox.py 统一绑定），模型看不到、也改不了这个参数。
返回结果里每条证据都带 id，契约据此判断模型引用的出处是否真实。
两类情况分开：查无数据返回 {"found": false, ...}，是正常结果；代码或数据出错直接抛异常，由循环转“调查未完成”。
"""

from __future__ import annotations

import difflib

import numpy as np
import pandas as pd

from .adapters import SIGNALS, load_equipment, load_failures, load_maintenance, load_sensors, load_spare_parts, short_code
from .priority import event_state, possible_orders
from .detection import (
    BASELINE_H, MERGE_GAP_H, MIN_BASELINE_POINTS, SUSTAINED, events_as_of, has_spread, official_detection,
)

AGENT_SIGNALS = ("vib", "temp", "current")  # 转速、负载率、湿度是噪声，不给 agent 下结论用
TREND_SIGMA = 0.5                           # 前后半段均值差超过 0.5σ 才算上升 / 下降
TIME = "%Y-%m-%d %H:%M"


def _not_found(message: str, **extra) -> dict:
    return {"found": False, "message": message, **extra}


def _equipment_ids() -> list[str]:
    return load_equipment()["equipment_id"].tolist()


def _unknown_equipment(equipment_id: str) -> dict:
    return _not_found(f"没有设备 {equipment_id}",
                      candidates=difflib.get_close_matches(equipment_id, _equipment_ids(), n=3, cutoff=0.5))


def _window_stats(equipment_id: str, signal: str, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """[start, end] 窗口的均值，对比窗口之前最近 168 个“振动未告警、不缺失”的小时。"""
    sensors = load_sensors()
    hourly = official_detection().hourly
    device = sensors[sensors["equipment_id"] == equipment_id].set_index("time")[signal]
    alarm = hourly[hourly["equipment_id"] == equipment_id].set_index("time")["vib_alarm"]
    window = device[(device.index >= start) & (device.index <= end)]
    before = device[device.index < start]
    base = before[~alarm.reindex(before.index, fill_value=False) & before.notna()].tail(BASELINE_H)
    return summarize(window, base)


def summarize(window: pd.Series, base: pd.Series) -> dict:
    """窗口相对基线的统计：均值、最大值、偏离几个 σ、趋势、缺失几小时。算不出时写明原因，不给出 z。"""
    values = window.dropna()
    missing = int(window.isna().sum())
    if len(base) < MIN_BASELINE_POINTS:
        return {"enough_data": False, "reason": "基线有效数据不足 3 天", "missing_hours": missing}
    if values.empty:
        return {"enough_data": False, "reason": "窗口内没有有效值", "missing_hours": missing}
    mean, base_mean, base_std = values.mean(), base.mean(), base.std()
    if not has_spread(base_std, base_mean):
        return {"enough_data": False, "reason": "基线没有波动，算不出偏离", "missing_hours": missing}
    half = len(values) // 2
    shift = (values.iloc[half:].mean() - values.iloc[:half].mean()) / base_std if half else 0.0
    trend = "上升" if shift > TREND_SIGMA else "下降" if shift < -TREND_SIGMA else "平稳"
    return {
        "enough_data": True,
        "mean": round(float(mean), 3), "max": round(float(values.max()), 3),
        "baseline_mean": round(float(base_mean), 3), "baseline_std": round(float(base_std), 3),
        "z": round(float((mean - base_mean) / base_std), 2), "trend": trend,
        "missing_hours": missing,
    }


def get_event(event_id: str, as_of: pd.Timestamp) -> dict:
    """事件信息，只算到 as_of：持续多久、峰值多少、是否还在进行。"""
    events = events_as_of(official_detection(), as_of)
    match = events[events["id"] == event_id]
    if match.empty:
        return _not_found(f"截至 {as_of:{TIME}} 没有事件 {event_id}")
    event = match.iloc[0].to_dict()
    result = {
        "id": event["id"], "kind": event["kind"], "equipment_id": event["equipment_id"],
        "signals": [SIGNALS[s][0] for s in event["signals"]],
        "start": f"{event['start']:{TIME}}", "last_abnormal": f"{event['end']:{TIME}}",
        "hours_so_far": int((event["end"] - event["start"]) / pd.Timedelta(hours=1)) + 1,
        "alarm_hours": int(event["alarm_hours"]),
        "peak_z": float(event["peak_z"]), "peak_at": f"{event['peak_at']:{TIME}}",
    }
    if event["kind"] == SUSTAINED:
        result["state"] = "进行中" if as_of - event["end"] <= pd.Timedelta(hours=MERGE_GAP_H) else "已结束"
        result["companions"] = [  # 同一时段温度、电流有没有一起变
            {"signal": SIGNALS[s][0], **_window_stats(event["equipment_id"], s, event["start"], event["end"])}
            for s in ("temp", "current")
        ]
    else:
        result["state"] = "已结束"
        hourly = official_detection().hourly
        at_peak = hourly[(hourly["equipment_id"] == event["equipment_id"]) & (hourly["time"] == event["peak_at"])]
        vib_z = at_peak["vib_z"].iloc[0] if len(at_peak) else np.nan
        result["vib_z_at_peak"] = None if pd.isna(vib_z) else round(float(vib_z), 2)
    return result


def get_equipment(equipment_id: str) -> dict:
    """设备台账：静态数据，不按时间过滤。"""
    table = load_equipment()
    match = table[table["equipment_id"] == equipment_id]
    if match.empty:
        return _unknown_equipment(equipment_id)
    row = match.iloc[0]
    return {
        "id": row["equipment_id"], "name": row["name"], "line": row["line"],
        "commissioned": row["commissioned"], "maint_cycle_days": int(row["maint_cycle_days"]),
        "key_parts": list(row["key_parts"]),
    }


def signal_summary(equipment_id: str, signal: str, as_of: pd.Timestamp, hours: int = 24) -> dict:
    """截至 as_of 最近 hours 小时的信号摘要：均值、基线、偏离几个 σ、趋势、缺失几小时。"""
    if equipment_id not in _equipment_ids():
        return _unknown_equipment(equipment_id)
    if signal not in AGENT_SIGNALS:
        return _not_found(f"信号只能是 {', '.join(AGENT_SIGNALS)}（{signal} 是噪声或不存在）")
    if not isinstance(hours, int) or not 1 <= hours <= BASELINE_H:
        return _not_found(f"hours 要在 1 到 {BASELINE_H} 之间")
    start = as_of - pd.Timedelta(hours=hours - 1)
    name, unit = SIGNALS[signal]
    return {
        "id": f"SIG-{short_code(equipment_id)}-{signal}-{hours}h-{as_of:%m%d}T{as_of:%H}",
        "equipment_id": equipment_id, "signal": name, "unit": unit,
        "window": [f"{start:{TIME}}", f"{as_of:{TIME}}"],
        **_window_stats(equipment_id, signal, start, as_of),
    }


def get_failure_history(equipment_id: str, as_of: pd.Timestamp) -> dict:
    """这台设备在 as_of 之前已经修完、填好的故障维修单（发生时间 + 停机时长 ≤ as_of）。"""
    if equipment_id not in _equipment_ids():
        return _unknown_equipment(equipment_id)
    failures = load_failures()
    visible = failures[(failures["equipment_id"] == equipment_id) & (failures["visible_from"] <= as_of)]
    records = [
        {
            "id": row["id"], "kind": "历史故障工单", "occurred_at": f"{row['occurred_at']:{TIME}}", "symptom": row["symptom"],
            "cause": row["cause"], "downtime_min": int(row["downtime_min"]), "cost": int(row["cost"]),
            "lost_qty": int(row["lost_qty"]), "action": row["action"],
        }
        for row in visible.sort_values("occurred_at", ascending=False).to_dict("records")
    ]
    return {
        "equipment_id": equipment_id, "records": records,
        "note": "历史工单只说明这台设备过去出过什么问题，不能证明本次是同一原因",
    }


def get_alarm_history(equipment_id: str, as_of: pd.Timestamp, days: int = 7) -> dict:
    """这台设备在 as_of 之前 days 天里出过的事件（本系统按检测规则生成的异常履历），新的在前。"""
    if equipment_id not in _equipment_ids():
        return _unknown_equipment(equipment_id)
    if not isinstance(days, int) or not 1 <= days <= 30:
        return _not_found("days 要在 1 到 30 之间")
    since = as_of - pd.Timedelta(days=days)
    events = events_as_of(official_detection(), as_of)
    mine = events[(events["equipment_id"] == equipment_id) & (events["end"] >= since)]
    records = [
        {
            "id": e["id"], "kind": e["kind"], "signals": [SIGNALS[s][0] for s in e["signals"]],
            "start": f"{e['start']:{TIME}}", "last_abnormal": f"{e['end']:{TIME}}",
            "alarm_hours": int(e["alarm_hours"]), "peak_z": float(e["peak_z"]), "state": event_state(e, as_of),
        }
        for e in mine.sort_values("start", ascending=False).to_dict("records")
    ]
    return {
        "equipment_id": equipment_id, "window": [f"{since:{TIME}}", f"{as_of:{TIME}}"], "records": records,
        "note": "本系统按检测规则生成的事件，含正在调查的这个事件；只说明出过哪些异常，不说明原因",
    }


def get_affected_orders(line: str, as_of: pd.Timestamp) -> dict:
    """可能受影响的生产工单：可以在这条产线生产的订单。不是实际排产，订单何时可知也无法判断。"""
    lines = sorted(set(load_equipment()["line"]))
    if line not in lines:
        return _not_found(f"没有产线 {line}", candidates=lines)
    orders = possible_orders(line, as_of)
    rank = {"战略客户": 0, "重点客户": 1, "一般客户": 2}
    orders = orders.assign(_rank=orders["customer_level"].map(rank)).sort_values(["_rank", "due"])
    records = [
        {"id": o["id"], "kind": "生产工单", "product": o["product"], "qty": int(o["qty"]),
         "customer_level": o["customer_level"], "due": f"{o['due']:%Y-%m-%d}"}
        for o in orders.to_dict("records")
    ]
    return {
        "line": line, "orders": records, "known_at": "未知",
        "note": "可在这条产线生产的订单，不是实际排产；官方数据没有订单创建时间，调查时点是否已知无法判断，结论只能写“可能受影响”",
    }


def get_maintenance(equipment_id: str, as_of: pd.Timestamp, days: int = 60) -> dict:
    """as_of 之前 days 天里做完的保养（结束时间才算可见），加上最近一次保养当时就排好的下次计划日期。"""
    if equipment_id not in _equipment_ids():
        return _unknown_equipment(equipment_id)
    if not isinstance(days, int) or not 1 <= days <= 365:
        return _not_found("days 要在 1 到 365 之间")
    table = load_maintenance()
    if table is None:
        return _not_found("维保记录还没接入（合成补充表 data/synthetic/maintenance.csv 待补）")
    done = table[(table["equipment_id"] == equipment_id) & (table["end_time"] <= as_of)].sort_values("end_time")
    since = as_of - pd.Timedelta(days=days)
    records = [
        {"id": r["record_id"], "component": r["component"], "maint_type": r["maint_type"],
         "start": f"{r['start_time']:{TIME}}", "end": f"{r['end_time']:{TIME}}", "content": r["content"],
         "result": r["result"]}
        for r in done[done["end_time"] >= since].iloc[::-1].to_dict("records")
    ]
    result = {"equipment_id": equipment_id, "window": [f"{since:{TIME}}", f"{as_of:{TIME}}"], "records": records,
              "last": None, "next_planned": None,
              "note": "维保记录是明确标注的合成数据；下次计划是最近一次保养当时就排好的日期，可以提前看到"}
    if len(done):
        last = done.iloc[-1]
        result["last"] = {"id": last["record_id"], "end": f"{last['end_time']:{TIME}}", "component": last["component"],
                          "days_since_last": int((as_of - last["end_time"]) / pd.Timedelta(days=1))}
        if pd.notna(last["next_planned_date"]):
            result["next_planned"] = {"date": f"{last['next_planned_date']:%Y-%m-%d}", "from": last["record_id"],
                                      "days_to_next": int((last["next_planned_date"] - as_of) / pd.Timedelta(days=1))}
    return result


def check_spare_parts(component: str, as_of: pd.Timestamp) -> dict:
    """按调查时点查某个部件的最新库存快照；没有带时点的快照就不返回当前库存，避免回放泄露未来数据。"""
    key_parts = sorted({p for parts in load_equipment()["key_parts"] for p in parts})
    if not any(p in component for p in key_parts):
        return _not_found(f"台账关键部件里没有 {component}", candidates=key_parts)
    table = load_spare_parts()
    if table is None:
        return _not_found("备件表还没接入（合成补充表 data/synthetic/spare_parts.csv 待补）")
    table = table[table["snapshot_at"] <= as_of]
    if table.empty:
        return _not_found(f"{as_of:{TIME}} 之前没有库存快照，不能用当前库存代替")
    table = table.sort_values("snapshot_at").drop_duplicates("part_id", keep="last")
    match = table[[p in component or component in p for p in table["component"]]]
    parts = [
        {"id": r["part_id"], "name": r["part_name"], "equipment_type": r["equipment_type"],
         "component": r["component"], "stock_qty": int(r["stock_qty"]), "safety_stock": int(r["safety_stock"]),
         "below_safety_stock": bool(r["stock_qty"] < r["safety_stock"]), "lead_time_days": int(r["lead_time_days"]),
         "location": r["location"], "snapshot_at": f"{r['snapshot_at']:{TIME}}"}
        for r in match.to_dict("records")
    ]
    return {"component": component, "parts": parts,
            "note": "库存是当前快照（合成数据），没有历史记录；低于安全库存时要考虑采购周期"}
