"""设备总览（功能树 1.2）：每台设备在当前时刻的状态灯和振动偏离（纯代码，规则写死、页面上写明）。

    告警中：  有进行中的持续偏离事件（最后一次告警后 12 小时内都算进行中，数据缺失不会把告警清掉）
    数据不足：当前这一小时算不出振动偏离（数据缺失，或刚开始还没攒够基线），不能说正常
    关注：    过去 24 小时内有事件（尖峰被确认，或持续偏离刚结束）
    正常：    其他
当前值缺失时 vib_z 为空，不拿过去的值顶替；另给最近一个有效值和它的时间（last_z、last_z_at）。
"""

from __future__ import annotations

import pandas as pd

from .adapters import load_equipment
from .detection import SUSTAINED, Detection, events_as_of
from .priority import ONGOING, event_state

WATCH_H = 24
ALARMING, NO_DATA, WATCHING, NORMAL = "告警中", "数据不足", "关注", "正常"


def device_overview(detection: Detection, as_of: pd.Timestamp, equipment: pd.DataFrame | None = None) -> pd.DataFrame:
    equipment = load_equipment() if equipment is None else equipment
    events = events_as_of(detection, as_of)
    hourly = detection.hourly[detection.hourly["time"] <= as_of]
    rows = []
    for device in equipment.to_dict("records"):
        mine = events[events["equipment_id"] == device["equipment_id"]]
        ongoing = [e for e in mine.to_dict("records") if e["kind"] == SUSTAINED and event_state(e, as_of) == ONGOING]
        recent = mine[mine["end"] >= as_of - pd.Timedelta(hours=WATCH_H)]
        own = hourly[hourly["equipment_id"] == device["equipment_id"]]
        now = own[own["time"] == as_of.floor("h")]["vib_z"].dropna()
        valid = own.dropna(subset=["vib_z"])
        z = round(float(now.iloc[0]), 2) if len(now) else None
        status = ALARMING if ongoing else NO_DATA if z is None else WATCHING if len(recent) else NORMAL
        latest = mine.sort_values("start").iloc[-1] if len(mine) else None
        rows.append({
            "equipment_id": device["equipment_id"], "name": device["name"], "line": device["line"],
            "status": status, "vib_z": z,
            "last_z": round(float(valid["vib_z"].iloc[-1]), 2) if len(valid) else None,
            "last_z_at": valid["time"].iloc[-1] if len(valid) else None,
            "latest_event": None if latest is None else latest["id"],
            "latest_kind": None if latest is None else latest["kind"],
        })
    return pd.DataFrame(rows)
