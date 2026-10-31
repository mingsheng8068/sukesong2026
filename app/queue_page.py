"""① 事件队列：当前时刻能看到的事件，按规则打分排序，分数的组成一列一列摊开。"""

from __future__ import annotations

import streamlit as st

from app.common import open_event, signal_names
from pdm.detection import SPIKE, SUSTAINED, events_as_of, official_detection
from pdm.priority import ORDERS_KNOWN_NOTE, rank_events

COLUMNS = {
    "id": "事件", "kind": "类型", "signal_names": "信号", "equipment_id": "设备", "line": "产线", "state": "状态",
    "start_text": "开始", "end_text": "最近异常", "alarm_hours": "告警小时", "peak_z": "峰值 z",
    "deviation_points": "偏离分", "duration_points": "持续分", "impact_points": "影响分",
    "orders_text": "候选订单 战略/重点/一般", "criticality": "重要性", "factor": "系数", "score": "总分",
}


def render(store) -> None:
    as_of = st.session_state["as_of"]
    detection = official_detection()
    events = events_as_of(detection, as_of)
    st.title("事件队列")

    hourly = detection.hourly
    vib_hours = int(hourly.loc[hourly["time"] <= as_of, "vib_alarm"].sum())
    spikes = events[events["kind"] == SPIKE]
    st.markdown(f"截至 **{as_of:%m-%d %H:%M}**：告警 **{vib_hours + int(spikes['alarm_hours'].sum())}** 小时 → "
                f"事件 **{len(events)}** 个（持续偏离 {int((events['kind'] == SUSTAINED).sum())}，"
                f"疑似传感器尖峰 {len(spikes)}）")
    if events.empty:
        st.info("这个时刻之前还没有事件。")
        return

    ranked = rank_events(events, as_of)
    view = ranked.assign(
        signal_names=ranked["signals"].map(signal_names),
        start_text=ranked["start"].dt.strftime("%m-%d %H:%M"),
        end_text=ranked["end"].dt.strftime("%m-%d %H:%M"),
        orders_text=[f"{a}/{b}/{c}" for a, b, c in
                     zip(ranked["orders_strategic"], ranked["orders_key"], ranked["orders_general"])],
    )[list(COLUMNS)].rename(columns=COLUMNS)
    st.dataframe(view, hide_index=True)
    st.caption("排序：进行中的事件在前，同组按总分降序。总分 =（偏离分 + 持续分 + 影响分）× 重要性系数，"
               f"规则打分，权重可调（pdm/priority.py）。尖峰的偏离分固定为 5，不按它的 z 算。"
               f"候选订单：可在该产线生产的订单，{ORDERS_KNOWN_NOTE}。重要性：同事的表到之前为“待补”，系数按 1。")

    labels = {r["id"]: f"{r['id']} · {r['kind']} · {r['state']} · 总分 {r['score']}" for r in ranked.to_dict("records")}
    choice = st.selectbox("选择事件", list(labels), format_func=labels.get, key="queue_pick")
    st.button("查看详情 →", type="primary", on_click=open_event, args=(choice,), key="open_event")
