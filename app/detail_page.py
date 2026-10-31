"""② 事件详情：调查、结论先行、曲线、证据（点开看出处原文）、调查过程、按状态分流到审批。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from agent.contract import INCOMPLETE, RISKS
from agent.llm import OpenAICompatLLM
from agent.loop import run_investigation
from agent.mock import broken_toolbox, scripted_llm
from agent.toolbox import Toolbox
from app.charts import signal_chart
from app.common import OFFLINE, equipment_info, go_to, signal_names, status_badge
from pdm.detection import events_as_of, official_detection
from pdm.priority import event_state
from pdm.workorders import (
    APPROVER, FALSE_ALARM_LABEL, DecisionError, approve, binding_problem, close_false_alarm, new_investigation,
    reject,
)


def _passed(investigation: dict) -> bool:
    """报告通过了契约才按报告渲染；没通过的报告字段可能缺失或类型不对，一律不读。"""
    return (investigation["report"] is not None and not investigation["problems"]
            and investigation["status"] != INCOMPLETE)


def _investigate(store, event_id: str, as_of: pd.Timestamp, model: str) -> None:
    """重新调查一开始，旧报告就失去审批资格；新记录保存成功后才绑定。失败时旧报告只能只读查看。"""
    current = st.session_state.setdefault("investigations", {})
    retired = st.session_state.setdefault("retired", {})
    retired[event_id] = {"id": current.pop(event_id, None), "error": "重新调查没有完成，上一份报告已失去审批资格"}
    scenario = st.session_state.get("scenario", "ok")
    if model == OFFLINE:
        llm = scripted_llm(event_id, scenario)
        toolbox = broken_toolbox(as_of) if scenario == "tool_error" else Toolbox(as_of)
    else:
        llm, toolbox = OpenAICompatLLM.from_env(), Toolbox(as_of)  # 真实模型失败就是“调查未完成”，不换脚本模型
    with st.spinner("agent 正在查证据…"):
        result = run_investigation(event_id, as_of, llm, toolbox)
    investigation = new_investigation(result, model)
    try:
        store.save_investigation(investigation)
    except OSError as exc:
        retired[event_id]["error"] = f"新的调查记录保存失败，不能进入审批（上一份报告也已失去审批资格）：{exc}"
        return
    retired.pop(event_id)
    current[event_id] = investigation["id"]


def _conclusion(investigation: dict) -> None:
    report, status = investigation["report"], investigation["status"]
    st.markdown(f"### {status_badge(status)}")
    if not _passed(investigation):
        st.error("调查未完成：转人工处理，不生成工单。")
        for problem in investigation["problems"]:
            st.markdown(f"- {problem}")
        return
    st.markdown(f"**{report['conclusion']}**")
    for action in report["actions"]:
        st.markdown(f"- 建议：{action['action']}（风险：{action['risk']}）")
    for part in report["suspected_parts"]:
        st.markdown(f"- 疑似部位：**{part['part']}** —— {part['reason']}")
    for part in report["ruled_out"]:
        st.markdown(f"- 已排除：{part['part']} —— {part['reason']}")
    if report["missing_evidence"]:
        st.markdown("待确认 / 还缺：" + "；".join(report["missing_evidence"]))
    st.caption(f"调查时点 {investigation['as_of']} · 模型 {investigation['model']} · {investigation['turns']} 轮 · "
               f"记录 {investigation['id']}")


def _editor_rows(table: pd.DataFrame) -> list[dict]:
    """把表格编辑器里的行整理干净：空值不算文字，没勾选不算确认。"""
    rows = []
    for row in table.to_dict("records"):
        action = row.get("action")
        rows.append({"action": action if isinstance(action, str) else "",
                     "risk": row.get("risk") if row.get("risk") in RISKS else None,
                     "risk_confirmed": row.get("risk_confirmed") is True})
    return rows


def _save(action) -> None:
    """提交决定。成功后立刻重跑页面：表单消失，换成从文件读回的决定；失败只报错，不显示成功。"""
    try:
        action()
    except DecisionError as exc:
        st.error(str(exc))
        return
    except OSError as exc:
        st.error(f"保存失败，没有生成任何记录：{exc}")
        return
    st.rerun()


def _approval(store, investigation: dict, event_id: str, as_of: pd.Timestamp, model: str) -> None:
    st.markdown(f"**人工审批**（审批人：{APPROVER}）")
    original = investigation["report"]["actions"]
    st.caption("原建议（只读）：" + "；".join(f"{a['action']}（{a['risk']}）" for a in original))
    with st.form("approval"):
        edited = st.data_editor(
            pd.DataFrame([{**a, "risk_confirmed": False} for a in original]), key="actions_editor",
            num_rows="dynamic", hide_index=True,
            column_config={
                "action": st.column_config.TextColumn("措施", required=True),
                "risk": st.column_config.SelectboxColumn("风险等级", options=list(RISKS), required=True),
                "risk_confirmed": st.column_config.CheckboxColumn("改过后重新确认风险"),
            },
        )
        st.caption("改了措施或新增措施，必须重新选风险等级并勾选确认；停机检修是“高”。")
        note = st.text_area("备注（可选）", key="approval_note")
        reason = st.text_input("驳回理由（驳回时必填）", key="reject_reason")
        approve_clicked = st.form_submit_button("通过并生成工单", type="primary")
        reject_clicked = st.form_submit_button("驳回")
    if approve_clicked:
        _save(lambda: approve(store, investigation, event_id, as_of, model, _editor_rows(edited), note))
    elif reject_clicked:
        _save(lambda: reject(store, investigation, event_id, as_of, model, reason))


def _next_step(store, investigation: dict, event_id: str, as_of: pd.Timestamp, model: str) -> None:
    decision = store.load_decision(investigation["id"])
    if decision:
        order = decision.get("work_order")
        st.success(f"已{decision['decision']}（{decision['decided_at']}，{decision['approver']}）"
                   + (f" · 工单 {order['id']}" if order else ""))
        if decision.get("label"):
            st.caption(decision["label"])
        st.button("去看工单与审批记录 →", on_click=go_to, args=("工单与审批记录",), key="to_orders")
        return
    if not _passed(investigation):
        return
    status, report = investigation["status"], investigation["report"]
    if status == "建议处置":
        _approval(store, investigation, event_id, as_of, model)
    elif status == "疑似误报":
        with st.form("false_alarm"):
            reason = st.text_input("确认理由（必填，例如现场检查结果）", key="false_alarm_reason")
            st.caption(FALSE_ALARM_LABEL)
            submitted = st.form_submit_button("确认误报并关闭")
        if submitted:
            _save(lambda: close_false_alarm(store, investigation, event_id, as_of, model, reason))
    elif status == "需持续观察":
        st.info("下一步：继续观察，有新数据后重新调查（拖动当前时刻再点调查即可）。")
    elif status == "需补充证据":
        st.info("下一步：补齐这些资料后重新调查：" + "；".join(report["missing_evidence"]))


def _evidence_and_trace(investigation: dict) -> None:
    if _passed(investigation):
        st.subheader("证据（点开看出处原文）")
        for item in investigation["report"]["evidence"]:
            with st.expander(f"{item['text']}　〔{item['source']}〕"):
                st.json(investigation["seen"].get(item["source"], {"说明": "快照里没有这条出处"}))
    else:
        st.markdown("**已取得的工具结果**（报告没通过检查，不作为结论依据，只供人工查看）")
        if not investigation["seen"]:
            st.caption("还没有取得任何工具结果。")
        for source, payload in investigation["seen"].items():
            with st.expander(source):
                st.json(payload)
    with st.expander("调查过程：agent 每一步为什么查、查了什么", expanded=False):
        for step in investigation["trace"]:
            st.markdown(f"**第 {step['turn']} 轮** · 目的：{step.get('purpose') or '未提供'}")
            note = f"（模型自带的 as_of={step['dropped_as_of']} 已丢弃）" if "dropped_as_of" in step else ""
            st.code(f"{step['tool']}({step['arguments']}){note}", language=None)
            st.json(step.get("result") or {"error": step.get("error")}, expanded=False)
        if investigation["problems"]:
            st.markdown("没通过的检查或出错：")
            for problem in investigation["problems"]:
                st.markdown(f"- {problem}")


def _charts(event: dict, as_of: pd.Timestamp) -> None:
    for signal in ("vib", "temp", "current"):
        st.altair_chart(signal_chart(event["equipment_id"], signal, as_of, event))
    with st.expander("转速、负载率、湿度（噪声信号，只展示，不用于判断）"):
        for signal in ("rpm", "load", "humidity"):
            st.altair_chart(signal_chart(event["equipment_id"], signal, as_of, event))


def render(store) -> None:
    as_of, model = st.session_state["as_of"], st.session_state["model"]
    event_id = st.session_state.get("event_id")
    st.title("事件详情" + (f" · {event_id}" if event_id else ""))
    if not event_id:
        st.info("先在事件队列里选一个事件。")
        return
    events = events_as_of(official_detection(), as_of)
    match = events[events["id"] == event_id]
    if match.empty:
        st.warning(f"站在 {as_of:%m-%d %H:%M} 还看不到这个事件（它在这之后才出现或才被确认）。")
        return
    event = match.iloc[0].to_dict()
    equipment = equipment_info(event["equipment_id"])
    st.caption(f"{equipment['name']}（{event['equipment_id']}，产线 {equipment['line']}） · {event['kind']}"
               f"（{signal_names(event['signals'])}） · {event_state(event, as_of)} · 开始 {event['start']:%m-%d %H:%M}"
               f" · 最近异常 {event['end']:%m-%d %H:%M} · 累计告警 {event['alarm_hours']} 小时 · 峰值 z={event['peak_z']}")

    st.button(f"调查（{model} · 时点 {as_of:%m-%d %H:%M}）", type="primary", key="investigate",
              on_click=_investigate, args=(store, event_id, as_of, model))
    investigation_id = st.session_state.get("investigations", {}).get(event_id)
    investigation = store.load_investigation(investigation_id) if investigation_id else None
    retired = st.session_state.get("retired", {}).get(event_id)

    left, right = st.columns([3, 2])
    with left:
        _charts(event, as_of)
    with right:
        if retired:
            st.error(retired["error"])
            previous = store.load_investigation(retired["id"]) if retired["id"] else None
            if previous:
                with st.expander("上一份报告（只读，不能审批）"):
                    _conclusion(previous)
        if investigation is None:
            if not retired:
                st.info("还没有调查。点上面的按钮，agent 会按当前时刻查证据。")
            return
        stale = binding_problem(investigation, event_id, as_of, model)
        if stale:
            st.warning(stale)
            with st.expander("查看这份过期报告（只读）"):
                _conclusion(investigation)
        else:
            _conclusion(investigation)
            st.divider()
            _next_step(store, investigation, event_id, as_of, model)
    if not stale:
        _evidence_and_trace(investigation)
