"""事件优先级：规则打分，依据全部摊开（纯代码，不调模型）。

    排序：进行中的事件在前，同组按分数降序
    分数 =（偏离分 + 持续分 + 影响分）× 重要性系数

权重是拍定的，可调，不包装成模型。生产工单只有交期、没有创建时间，某个时刻是否已经知道这张订单无法判断，
所以默认只把候选关联订单列出来、不计分（DECISIONS D28）。
"""

from __future__ import annotations

import pandas as pd

from .adapters import load_criticality, load_equipment, load_orders
from .detection import MERGE_GAP_H, SPIKE

ASSUME_ORDERS_KNOWN = False   # 打开时假设所有订单在回放开始前已知，页面要同步标注
Z_CAP, Z_WEIGHT = 10, 5       # 偏离分 = min(峰值 z, 10) × 5
SPIKE_DEVIATION = 5           # 尖峰单小时、单信号，偏离分固定，不按它的 z 算
MAX_DURATION = 24             # 持续分 = min(累计告警小时, 24)
CUSTOMER_POINTS = {"战略客户": 3, "重点客户": 2, "一般客户": 1}
MAX_IMPACT = 20
CRITICALITY_FACTOR = {"A": 1.5, "B": 1.0, "C": 0.7}
ORDERS_KNOWN_NOTE = "可知时间未知，不计分"

ONGOING, ENDED = "进行中", "已结束"


def possible_orders(line: str, as_of: pd.Timestamp, orders: pd.DataFrame | None = None) -> pd.DataFrame:
    """可以在这条产线生产的工单（候选关联订单）。

    as_of 先预留：官方数据没有订单创建时间，现在不按它过滤，结果里标“可知时间未知”。
    """
    orders = load_orders() if orders is None else orders
    match = orders[orders["lines"].apply(lambda lines: line in lines)].copy()
    match["known_at"] = "未知"
    return match


def event_state(event: dict, as_of: pd.Timestamp) -> str:
    if event["kind"] == SPIKE:
        return ENDED
    return ONGOING if as_of - event["end"] <= pd.Timedelta(hours=MERGE_GAP_H) else ENDED


def rank_events(events: pd.DataFrame, as_of: pd.Timestamp, equipment: pd.DataFrame | None = None,
                criticality: pd.DataFrame | None = None, orders: pd.DataFrame | None = None,
                assume_orders_known: bool = ASSUME_ORDERS_KNOWN) -> pd.DataFrame:
    """给 as_of 时能看到的事件打分并排序，每个分项单独一列。"""
    equipment = load_equipment() if equipment is None else equipment
    criticality = load_criticality() if criticality is None else criticality
    lines = dict(zip(equipment["equipment_id"], equipment["line"]))
    levels = dict(zip(criticality["equipment_id"], criticality["criticality"]))

    rows = []
    for event in events.to_dict("records"):
        line = lines.get(event["equipment_id"], "")
        candidates = possible_orders(line, as_of, orders)
        counts = {level: int((candidates["customer_level"] == level).sum()) for level in CUSTOMER_POINTS}
        deviation = SPIKE_DEVIATION if event["kind"] == SPIKE else min(event["peak_z"], Z_CAP) * Z_WEIGHT
        duration = min(int(event["alarm_hours"]), MAX_DURATION)
        impact = min(sum(CUSTOMER_POINTS[k] * n for k, n in counts.items()), MAX_IMPACT) if assume_orders_known else 0
        level = levels.get(event["equipment_id"])
        factor = CRITICALITY_FACTOR.get(level, 1.0)
        rows.append({
            **event, "line": line, "state": event_state(event, as_of),
            "deviation_points": round(deviation, 1), "duration_points": duration, "impact_points": impact,
            "orders_strategic": counts["战略客户"], "orders_key": counts["重点客户"], "orders_general": counts["一般客户"],
            "criticality": level if level in CRITICALITY_FACTOR else "待补", "factor": factor,
            "score": round((deviation + duration + impact) * factor, 1),
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    table["_ongoing"] = table["state"] == ONGOING
    table = table.sort_values(["_ongoing", "score", "start"], ascending=[False, False, False], kind="stable")
    return table.drop(columns="_ongoing").reset_index(drop=True)
