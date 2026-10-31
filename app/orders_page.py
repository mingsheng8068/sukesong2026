"""③ 工单与审批记录：所有决定（通过 / 驳回 / 确认误报并关闭）和生成的工单。"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _work_order(order: dict) -> None:
    st.markdown(f"**{order['id']}** · {order['status']} · {order['label']}")
    st.markdown(f"设备：{order['equipment_name']}（{order['equipment_id']}，产线 {order['line']}） · 事件 {order['event_id']}")
    st.markdown(f"故障描述：{order['description']}")
    for part in order["suspected_parts"]:
        st.markdown(f"- 疑似部位：{part['part']} —— {part['reason']}（依据 {'、'.join(part['evidence'])}）")
    st.markdown("建议措施：")
    st.dataframe(pd.DataFrame(order["actions"]).rename(columns={"action": "措施", "risk": "风险等级"}), hide_index=True)
    st.markdown("证据：")
    for item in order["evidence"]:
        st.markdown(f"- {item['text']}〔{item['source']}〕")
    st.markdown(f"可能受影响的订单 {len(order['possible_orders'])} 张（{order['possible_orders_note']}）")
    if isinstance(order["spare_parts"], list):
        st.markdown("所需备件（按疑似部位查，库存是审批时的快照）：")
        st.dataframe(pd.DataFrame(order["spare_parts"])[["id", "name", "stock_qty", "safety_stock", "lead_time_days"]]
                     .rename(columns={"id": "备件", "name": "名称", "stock_qty": "库存", "safety_stock": "安全库存",
                                      "lead_time_days": "采购周期（天）"}), hide_index=True)
    else:
        st.markdown(f"所需备件：{order['spare_parts']}")
    if order["note"]:
        st.markdown(f"备注：{order['note']}")
    st.caption(f"审批人 {order['approver']} · 审批时间 {order['approved_at']} · 调查时点 {order['investigation_as_of']}"
               f" · 模型 {order['model']} · 调查记录 {order['investigation_id']}")


def render(store) -> None:
    st.title("工单与审批记录")
    decisions = store.list_decisions()
    if not decisions:
        st.info("还没有审批记录。")
        return
    st.dataframe(pd.DataFrame([{
        "决定": d["decision"], "工单号": (d.get("work_order") or {}).get("id", ""), "事件": d["event_id"],
        "调查时点（模拟）": d["investigation_as_of"], "审批时间（实际）": d["decided_at"], "审批人": d["approver"],
        "人工改过措施": "是" if d.get("modified") else "", "理由 / 备注": d["reason"],
    } for d in reversed(decisions)]), hide_index=True)
    for d in reversed(decisions):
        title = f"{d['decision']} · {(d.get('work_order') or {}).get('id', d['event_id'])} · {d['decided_at']}"
        with st.expander(title):
            if d.get("work_order"):
                _work_order(d["work_order"])
                if d.get("modified"):
                    st.caption("原建议：" + "；".join(f"{a['action']}（{a['risk']}）" for a in d["original_actions"]))
            else:
                st.markdown(f"事件 {d['event_id']} · 理由：{d['reason']}")
                if d.get("label"):
                    st.caption(d["label"])
