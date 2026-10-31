"""调查初稿的输出契约和校验（按 8D 的问题描述、原因、遏制、措施组织）。

证据纪律里能写成规则的部分都放在这里检查，不只靠提示词让模型自觉。
"""

from __future__ import annotations

from typing import Any

STATUSES = {"需补充证据", "可提交审批", "未找到相似案例", "检索失败"}
CAUSE_KINDS = {"发生原因", "流出原因"}
REQUIRED_FIELDS = (
    "problem", "facts", "similar_cases", "hypotheses",
    "containment", "reference_actions", "missing_evidence", "status",
)

REPORT_TEMPLATE = """{
  "problem": {"part": "零件", "batch": "批次", "defect": "缺陷", "claimed_qty": "客户声称的数量，没有就写 null", "source": "客诉 id"},
  "facts": [{"text": "事实", "source": "工具结果里的 id"}],
  "similar_cases": [{"case_id": "CASE-xxx", "similarity": 0.0}],
  "hypotheses": [{"cause": "疑似原因", "kind": "发生原因 | 流出原因", "status": "疑似", "evidence": ["id"]}],
  "containment": ["临时遏制建议"],
  "reference_actions": [{"action": "参考措施", "from_case": "CASE-xxx", "verified": false}],
  "missing_evidence": ["还缺的记录"],
  "status": "需补充证据 | 可提交审批 | 未找到相似案例 | 检索失败"
}"""


def collect_ids(value: Any) -> set[str]:
    """收集工具结果里出现过的所有 id，用来判断引用是否真实存在。"""
    ids: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            ids.add(value["id"])
        for item in value.values():
            ids |= collect_ids(item)
    elif isinstance(value, list):
        for item in value:
            ids |= collect_ids(item)
    return ids


def collect_case_effective(value: Any) -> dict[str, Any]:
    """收集检索结果里历史案例的 effective（措施是否已验证有效）。"""
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        case_id = value.get("id")
        if isinstance(case_id, str) and case_id.startswith("CASE-") and "effective" in value:
            found[case_id] = value["effective"]
        for item in value.values():
            found.update(collect_case_effective(item))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_case_effective(item))
    return found


def validate_report(report: Any, seen_ids: set[str], case_effective: dict[str, Any] | None = None) -> list[str]:
    """返回违反契约的问题清单；空列表表示通过。"""
    if not isinstance(report, dict):
        return ["输出不是 JSON 对象"]
    problems = [f"缺少字段 {key}" for key in REQUIRED_FIELDS if key not in report]
    if problems:
        return problems
    case_effective = case_effective or {}

    problem = report["problem"]
    if not isinstance(problem, dict) or problem.get("source") not in seen_ids:
        problems.append("problem.source 必须是本次查到的客诉 id")

    for i, fact in enumerate(report["facts"]):
        source = fact.get("source")
        if not source:
            problems.append(f"facts[{i}] 没有出处")
        elif source not in seen_ids:
            problems.append(f"facts[{i}] 的出处 {source} 不在本次工具结果里")

    for i, case in enumerate(report["similar_cases"]):
        if case.get("case_id") not in seen_ids:
            problems.append(f"similar_cases[{i}] 的 {case.get('case_id')} 不在本次检索结果里")

    for i, hypothesis in enumerate(report["hypotheses"]):
        if hypothesis.get("status") != "疑似":
            problems.append(f"hypotheses[{i}] 必须标为“疑似”")
        if hypothesis.get("kind") not in CAUSE_KINDS:
            problems.append(f"hypotheses[{i}] 的 kind 必须是“发生原因”或“流出原因”")
        evidence = hypothesis.get("evidence") or []
        if not evidence:
            problems.append(f"hypotheses[{i}] 没有证据")
        for source in evidence:
            if source not in seen_ids:
                problems.append(f"hypotheses[{i}] 的证据 {source} 不在本次工具结果里")

    for i, action in enumerate(report["reference_actions"]):
        case_id = action.get("from_case")
        if case_id not in seen_ids:
            problems.append(f"reference_actions[{i}] 引用的 {case_id} 不在本次检索结果里")
            continue
        should_be_verified = case_effective.get(case_id) is True
        if bool(action.get("verified")) != should_be_verified:
            expected = "true" if should_be_verified else "false（措施尚未验证有效）"
            problems.append(f"reference_actions[{i}] 的 verified 应为 {expected}")

    status = report["status"]
    if status not in STATUSES:
        problems.append(f"status 不合法：{status}")
    if status == "可提交审批":
        if report["missing_evidence"]:
            problems.append("还有缺失证据时不能写“可提交审批”")
        if not report["hypotheses"]:
            problems.append("没有疑似原因时不能写“可提交审批”")

    return problems
