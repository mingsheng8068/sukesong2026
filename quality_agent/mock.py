"""离线演示脚本：不调真实模型，按固定步骤调查 C001，用来验证循环和契约。"""

from __future__ import annotations

import json

from .llm import LLMReply, ScriptedLLM, ToolCall


def _call(call_id: str, name: str, **arguments) -> LLMReply:
    return LLMReply(None, [ToolCall(call_id, name, json.dumps(arguments, ensure_ascii=False))])


def _final_report(messages: list[dict]) -> LLMReply:
    # 相似度照抄最近一次检索结果，不自己编数字
    search = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
    case = next(r for r in search["results"] if r["id"] == "CASE-031")
    report = {
        "problem": {"part": "笔记本电脑底壳", "batch": "H100-0925B", "defect": "边角刮花",
                    "claimed_qty": "抽检 200 个中 37 个", "source": "C001"},
        "facts": [
            {"text": "客户反馈 9 月 29 日到货的批次 H100-0925B 底壳边角刮花，抽检 200 个中 37 个", "source": "C001"},
            {"text": "该批次包装由纸隔板临时改为珍珠棉，未走变更评审", "source": "CHG-0924-PKG"},
            {"text": "出货检验在包装前抽检 80 件，0 不良", "source": "INS-H100-0925B-OQC"},
            {"text": "生产当天注塑参数在基线 ±2% 内，无报警", "source": "PR-IM03-0925"},
        ],
        "similar_cases": [{"case_id": "CASE-031", "similarity": case["similarity"]}],
        "hypotheses": [
            {"cause": "运输中包装防护不足（隔板临时改为珍珠棉）导致摩擦划伤", "kind": "发生原因",
             "status": "疑似", "evidence": ["CHG-0924-PKG", "CASE-031", "PR-IM03-0925"]},
            {"cause": "出货检验在包装前进行，覆盖不到运输后的状态", "kind": "流出原因",
             "status": "疑似", "evidence": ["INS-H100-0925B-OQC", "CASE-031"]},
        ],
        "containment": ["隔离在库和在途的 H100-0925B，开箱挑选", "排查 9 月 24 日之后其他使用珍珠棉包装的批次"],
        "reference_actions": [
            {"action": case["corrective_action"], "from_case": "CASE-031", "verified": case["effective"] is True}
        ],
        "missing_evidence": ["客户开箱照片和退回样品复检", "物流运输记录"],
        "status": "需补充证据",
    }
    return LLMReply(json.dumps(report, ensure_ascii=False))


def scripted_llm_for_demo(complaint_id: str) -> ScriptedLLM:
    if complaint_id != "C001":
        raise SystemExit("离线演示脚本只写了 C001；其他客诉请配置真实模型")
    return ScriptedLLM([
        _call("call_1", "get_complaint", complaint_id="C001"),
        _call("call_2", "get_lot", batch="H100-0925B"),
        _call("call_3", "get_inspections", batch="H100-0925B"),
        _call("call_4", "get_process_records", batch="H100-0925B"),
        _call("call_5", "search_similar_cases", query="注塑外壳 边角 刮花 划伤"),
        _final_report,
    ])
