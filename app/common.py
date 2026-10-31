"""页面共用的小工具：名称、标签、跳转。"""

from __future__ import annotations

import streamlit as st

from pdm.adapters import SIGNALS, load_equipment

OFFLINE = "离线脚本模型"
STATUS_COLORS = {"建议处置": "orange", "需补充证据": "blue", "需持续观察": "blue", "疑似误报": "green",
                 "调查未完成": "red"}


def open_event(event_id: str) -> None:
    """按钮回调：选中事件并切到详情页（回调里改页面状态，避免控件已创建后再改）。"""
    st.session_state["event_id"] = event_id
    st.session_state["page"] = "事件详情"


def go_to(page: str) -> None:
    st.session_state["page"] = page


def equipment_info(equipment_id: str) -> dict:
    table = load_equipment()
    match = table[table["equipment_id"] == equipment_id]
    return match.iloc[0].to_dict() if len(match) else {"name": equipment_id, "line": ""}


def signal_names(signals) -> str:
    return "、".join(SIGNALS[s][0] for s in signals)


def status_badge(status: str) -> str:
    return f":{STATUS_COLORS.get(status, 'gray')}-badge[{status}]"
