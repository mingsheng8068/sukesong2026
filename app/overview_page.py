"""⓪ 设备总览：8 台设备在当前时刻的状态灯，以及最近 72 小时的振动偏离（z）。"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app.common import open_event
from pdm.detection import Z_ALARM, official_detection
from pdm.overview import ALARMING, NO_DATA, NORMAL, WATCH_H, WATCHING, device_overview

HOURS = 72
BADGES = {ALARMING: ":red-badge[告警中]", NO_DATA: ":gray-badge[数据不足]", WATCHING: ":orange-badge[关注]",
          NORMAL: ":green-badge[正常]"}


def _z_chart(hourly: pd.DataFrame, as_of: pd.Timestamp) -> alt.Chart:
    start = as_of - pd.Timedelta(hours=HOURS)
    data = hourly[hourly["time"].between(start, as_of)][["time", "vib_z"]].rename(columns={"time": "时间", "vib_z": "振动 z"})
    x = alt.X("时间:T", title=None, axis=alt.Axis(format="%m-%d %H时", labelAngle=0), scale=alt.Scale(domain=[start, as_of]))
    line = alt.Chart(data).mark_line(strokeWidth=1.5).encode(x=x, y=alt.Y("振动 z:Q", title=None))
    threshold = alt.Chart(pd.DataFrame({"阈值": [Z_ALARM]})).mark_rule(color="#dc2626", strokeDash=[4, 4]).encode(
        y="阈值:Q")
    return alt.layer(line, threshold).properties(height=110)


def _z_text(device: dict) -> str:
    """当前值缺失就写缺失，不拿过去的值顶替；有最近有效值时附上它的时间。"""
    if not pd.isna(device["vib_z"]):
        return f"当前振动 z={device['vib_z']}"
    if pd.isna(device["last_z"]):
        return "当前振动 z 缺失（还没有有效值）"
    return f"当前振动 z 缺失（最近有效值 z={device['last_z']}，{device['last_z_at']:%m-%d %H:%M}）"


def render(store) -> None:
    as_of = st.session_state["as_of"]
    detection = official_detection()
    table = device_overview(detection, as_of)
    st.title("设备总览")
    counts = table["status"].value_counts()
    st.markdown(f"截至 **{as_of:%m-%d %H:%M}**："
                + " · ".join(f"{status} **{counts.get(status, 0)}** 台" for status in (ALARMING, NO_DATA, WATCHING, NORMAL)))
    st.caption(f"状态灯：有进行中的持续偏离事件 → 告警中（数据缺失不会清掉告警）；当前这一小时算不出振动偏离 → 数据不足；"
               f"过去 {WATCH_H} 小时内有事件（尖峰被确认或持续偏离刚结束）→ 关注；其他 → 正常。"
               f"小图是最近 {HOURS} 小时的振动偏离（z），红色虚线是告警阈值 {Z_ALARM}σ。")

    columns = st.columns(4)
    for i, device in enumerate(table.to_dict("records")):
        with columns[i % 4]:
            st.markdown(f"**{device['name']}** {BADGES[device['status']]}")
            st.caption(f"{device['equipment_id']} · 产线 {device['line']} · {_z_text(device)}")
            hourly = detection.hourly[detection.hourly["equipment_id"] == device["equipment_id"]]
            st.altair_chart(_z_chart(hourly, as_of))
            if isinstance(device["latest_event"], str):
                st.button(f"最近事件 {device['latest_event']} →", key=f"overview_{device['equipment_id']}",
                          on_click=open_event, args=(device["latest_event"],))
