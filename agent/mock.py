"""离线脚本模型：不调真实模型，按固定步骤调查，用来验证循环、契约和失败路径。

脚本里的“判断”是写死的（比如疑似部件取历史单里提到的关键部件，没有就取第一个），
只用来走通流程，不代表 agent 的推理。数字一律从工具结果里照抄。

场景：
- ok：完整调查，持续事件 → 建议处置，尖峰 → 疑似误报；
- insufficient：只查了事件和历史单就交卷 → 需补充证据；
- fake_id：引用一张不存在的维修单，两次都被契约打回 → 调查未完成；
- tool_error：查历史单时工具内部出错 → 调查未完成（配合 broken_toolbox）；
- future_as_of：模型自己填了一个未来的 as_of → 被丢弃，查到的仍是调查时点的数据；
- missing_fields / wrong_types：报告缺字段、字段类型不对，两次都被契约打回 → 调查未完成。
"""

from __future__ import annotations

import json

import pandas as pd

from .llm import LLMReply, ScriptedLLM, ToolCall
from .toolbox import TOOL_FUNCTIONS, Toolbox

SCENARIOS = ("ok", "insufficient", "fake_id", "tool_error", "future_as_of", "missing_fields", "wrong_types")


def _calls(*calls: tuple[str, dict], purpose: str | None = None) -> LLMReply:
    return LLMReply(purpose, [ToolCall(f"call_{i}_{name}", name, json.dumps(args, ensure_ascii=False))
                              for i, (name, args) in enumerate(calls)])


def _results(messages: list[dict]) -> list[dict]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def _find(results: list[dict], test) -> dict:
    return next((r for r in results if test(r)), {})


def _event(results: list[dict], event_id: str) -> dict:
    return _find(results, lambda r: r.get("id") == event_id)


def _by_tool(messages: list[dict], name: str) -> dict:
    """某个工具最近一次的结果（按 tool_call_id 对回调用）。"""
    names = {c["id"]: c["function"]["name"] for m in messages if m["role"] == "assistant"
             for c in m.get("tool_calls") or []}
    found = [json.loads(m["content"]) for m in messages if m["role"] == "tool" and names.get(m["tool_call_id"]) == name]
    return found[-1] if found else {}


def _lookups(event_id: str, extra: dict | None = None):
    """第二步：读事件结果，接着查台账、信号、异常履历、保养、历史单和知识库（一次发出多个调用）。"""
    def step(messages: list[dict]) -> LLMReply:
        equipment_id = _event(_results(messages), event_id)["equipment_id"]
        return _calls(
            ("get_equipment", {"equipment_id": equipment_id}),
            ("signal_summary", {"equipment_id": equipment_id, "signal": "vib"}),
            ("signal_summary", {"equipment_id": equipment_id, "signal": "temp"}),
            ("get_alarm_history", {"equipment_id": equipment_id}),
            ("get_maintenance", {"equipment_id": equipment_id}),
            ("get_failure_history", {"equipment_id": equipment_id, **(extra or {})}),
            ("search_knowledge", {"query": "振动升高 温度升高"}),
            purpose="查台账确认关键部件，看振动和温度最近 24 小时的变化，再看最近的异常、保养、历史故障和维修文档。",
        )
    return step


def _report(event_id: str, extra_evidence: list[dict] | None = None):
    def step(messages: list[dict]) -> LLMReply:
        results = _results(messages)
        event = _event(results, event_id)
        equipment = _find(results, lambda r: "key_parts" in r)
        records = _by_tool(messages, "get_failure_history").get("records", [])
        missing = [label for name, label in (("get_maintenance", "维保记录（合成数据待补）"),
                                             ("search_knowledge", "设备维修知识文档（待补）"))
                   if _by_tool(messages, name).get("found") is False]
        evidence = [{"text": f"{event['start']} 起{'、'.join(event['signals'])}偏离，到调查时点 {event['hours_so_far']} 小时，"
                             f"峰值 z={event['peak_z']}", "source": event_id}]
        for summary in results:
            if summary.get("id", "").startswith("SIG-") and summary.get("enough_data"):
                evidence.append({"text": f"{summary['window'][0]} 到 {summary['window'][1]}，{summary['signal']}均值 "
                                         f"{summary['mean']} {summary['unit']}，基线 {summary['baseline_mean']}，"
                                         f"偏离 {summary['z']}σ，趋势{summary['trend']}", "source": summary["id"]})
        for record in records[:2]:
            evidence.append({"text": f"{record['occurred_at']} 曾发生{record['symptom']}，原因记为{record['cause']}，"
                                     f"处理方式{record['action']}", "source": record["id"]})
        evidence += extra_evidence or []

        if event["kind"] == "疑似传感器尖峰":
            report = {
                "event_id": event_id, "status": "疑似误报",
                "conclusion": f"{equipment['name']}{'、'.join(event['signals'])}只跳了一下，振动没变，更像传感器问题，请确认后关闭",
                "evidence": evidence, "suspected_parts": [], "ruled_out": [],
                "actions": [{"action": "检查传感器接线和安装，确认后关闭事件", "risk": "低"}],
                "missing_evidence": [],
            }
        else:
            mentioned = [p for p in equipment["key_parts"] if any(p in r["cause"] for r in records)]
            part = (mentioned or equipment["key_parts"])[0]
            report = {
                "event_id": event_id, "status": "建议处置",
                "conclusion": f"{equipment['name']}振动持续升高，疑似{part}问题，建议加强监测并安排点检",
                "evidence": evidence,
                "suspected_parts": [{"part": part, "status": "疑似", "reason": "台账关键部件，振动持续偏离",
                                     "evidence": [event_id, equipment["id"]]}],
                "ruled_out": [],
                "actions": [{"action": f"加强对{part}的监测，安排点检", "risk": "低"}],
                "missing_evidence": missing,
            }
        return LLMReply(json.dumps(report, ensure_ascii=False))
    return step


def _insufficient(event_id: str):
    def step(messages: list[dict]) -> LLMReply:
        event = _event(_results(messages), event_id)
        report = {
            "event_id": event_id, "status": "需补充证据",
            "conclusion": "振动有偏离，但还没查台账和信号细节，资料不够，先补证据再判断",
            "evidence": [{"text": f"{event['start']} 起偏离，峰值 z={event['peak_z']}", "source": event_id}],
            "suspected_parts": [], "ruled_out": [], "actions": [],
            "missing_evidence": ["设备台账和关键部件", "振动、温度信号摘要", "维保记录"],
        }
        return LLMReply(json.dumps(report, ensure_ascii=False))
    return step


def scripted_llm(event_id: str, scenario: str = "ok") -> ScriptedLLM:
    first = _calls(("get_event", {"event_id": event_id}), purpose="先看事件本身：从什么时候开始、持续多久、偏离多大。")
    if scenario == "insufficient":
        return ScriptedLLM([first, _insufficient(event_id)])
    if scenario == "fake_id":
        fake = [{"text": "上月同部件也坏过", "source": "WO-2026-9999"}]
        return ScriptedLLM([first, _lookups(event_id), _report(event_id, fake), _report(event_id, fake)])
    if scenario == "future_as_of":
        return ScriptedLLM([first, _lookups(event_id, {"as_of": "2026-09-30 23:00"}), _report(event_id)])
    if scenario in ("missing_fields", "wrong_types"):
        bad = ({"status": "建议处置"} if scenario == "missing_fields" else
               {"event_id": event_id, "conclusion": "疑似问题", "status": "建议处置", "evidence": "不是列表",
                "suspected_parts": {"part": "x"}, "ruled_out": [], "actions": "加强监测", "missing_evidence": []})
        reply = LLMReply(json.dumps(bad, ensure_ascii=False))
        return ScriptedLLM([first, _lookups(event_id), reply, reply])
    if scenario in ("ok", "tool_error"):
        return ScriptedLLM([first, _lookups(event_id), _report(event_id)])
    raise SystemExit(f"没有场景 {scenario}，可选：{', '.join(SCENARIOS)}")


def broken_toolbox(as_of: pd.Timestamp) -> Toolbox:
    """查历史单时模拟数据源故障。"""
    def broken_failure_history(equipment_id: str, as_of: pd.Timestamp) -> dict:
        raise ConnectionError("维修工单库连接超时")
    return Toolbox(as_of, {**TOOL_FUNCTIONS, "get_failure_history": broken_failure_history})
