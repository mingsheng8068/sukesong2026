"""处置建议的输出契约和校验。

证据纪律里能写成规则的部分都在这里用代码检查，不只靠提示词让模型自觉。
契约只查形式：出处真实、测量值照抄、状态合法；出处是否真的支持结论，要靠人工核对和评测。
"""

from __future__ import annotations

import re
from typing import Any

STATUSES = ("疑似误报", "需持续观察", "需补充证据", "建议处置")
INCOMPLETE = "调查未完成"  # 只能由程序设置：工具出错、超过最大轮数、二次校验不过
RISKS = ("低", "高")
NON_CAUSAL_KINDS = {"历史故障工单"}
REQUIRED_FIELDS = {
    "event_id": str, "conclusion": str, "status": str, "evidence": list,
    "suspected_parts": list, "ruled_out": list, "actions": list, "missing_evidence": list,
}

REPORT_TEMPLATE = """{
  "event_id": "本次调查的事件 id",
  "conclusion": "一句白话结论，写给现场工程师看",
  "status": "疑似误报 | 需持续观察 | 需补充证据 | 建议处置",
  "evidence": [{"text": "事实，数字照抄工具结果", "source": "工具结果里的 id"}],
  "suspected_parts": [{"part": "台账关键部件", "status": "疑似", "reason": "为什么怀疑它", "evidence": ["id"]}],
  "ruled_out": [{"part": "排除的部件或原因", "reason": "为什么排除", "evidence": ["id"]}],
  "actions": [{"action": "建议措施", "risk": "低 | 高"}],
  "missing_evidence": ["还缺的资料"]
}"""

# 数字检查只查测量值。
# 第一步：带单位或紧跟 z 的数字（含“90-95 mm/s”“2.1~2.3σ”这样的区间）一定是测量值，先取出来，免得被下面的豁免吞掉；
# 第二步：剩下的文字去掉编号、代号、日期时间、时长、计数，它们不检查，也不能给测量值作证。
_SIGNED = r"[-−]?\d+(?:\.\d+)?"
_UNIT = r"(?:mm/s|℃|°C|σ|%|rpm|元|A(?![A-Za-z]))"
_MEASURED = re.compile(
    rf"(?<![A-Za-z])[zZ]\s*[=:：]?\s*{_SIGNED}"                     # z=3.2、z3.2
    rf"|{_SIGNED}(?:\s*[-~～–—至到]\s*{_SIGNED})?\s*{_UNIT}"         # 2.4 mm/s、90-95 mm/s、2.1~2.3σ
)
_ID = re.compile(r"[A-Z]{2,}(?:-[A-Za-z0-9]+)+")                    # EV-IM011-0920T23、WO-2026-1004
_CODE = re.compile(r"(?<![A-Za-z0-9.])[A-Za-z]+\d+[A-Za-z0-9]*")     # L03、CNC001
_MONTH, _DAY = r"(?:0?[1-9]|1[0-2])", r"(?:0?[1-9]|[12]\d|3[01])"
_HOUR, _MINUTE = r"(?:[01]?\d|2[0-3])", r"[0-5]\d"
_DATETIME = re.compile(                                              # 只认合法的月日时分，“90-95”不会被当成日期
    rf"(?<![\d.])(?:\d{{4}}-{_MONTH}-{_DAY}(?:[ T]{_HOUR}:{_MINUTE})?"   # 2026-09-21 14:00
    rf"|{_MONTH}-{_DAY}(?:[ T]{_HOUR}:{_MINUTE})?"                      # 09-21 14:00
    rf"|{_HOUR}:{_MINUTE}"                                              # 14:00
    rf"|{_MONTH}\s*月\s*{_DAY}\s*[日号]?"                                # 9 月 21 日
    rf"|{_HOUR}\s*点)(?![\d.])"                                         # 14 点
)
_DURATION = re.compile(r"\d+(?:\.\d+)?\s*(?:个)?\s*(?:小时|分钟|天|周|h(?![A-Za-z])|min(?![A-Za-z]))")
_COUNT = re.compile(r"\d+\s*(?:个|次|张|条|件|台|项|篇|处)")
_NUMBER = re.compile(r"(?<![\d.])[-−]?\d+(?:\.\d+)?")   # 紧跟在数字后面的“-”是区间，不当负号

# 时长和计数字段：文本里按时长、计数放行，也不拿来给测量值作证（否则“持续 16 小时”能证明“16σ”）；
# 知识库的检索分数 score 只用来排序，也不能作证（否则 score=3.7 能证明“振动 3.7 mm/s”）
NON_MEASUREMENT_FIELDS = {"hours", "hours_so_far", "alarm_hours", "missing_hours", "downtime_min",
                          "maint_cycle_days", "lost_qty", "days_since_last", "days_to_next", "lead_time_days",
                          "score"}


def index_by_id(value: Any) -> dict[str, dict]:
    """把工具结果里每个带 id 的对象按 id 收起来：引用的出处必须在这里面。

    同一个 id 可能出现在几个工具的结果里（比如异常履历里也列着正在调查的事件），合并字段，不互相覆盖。
    """
    found: dict[str, dict] = {}

    def add(record_id: str, record: dict) -> None:
        found[record_id] = {**found.get(record_id, {}), **record}

    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            add(value["id"], value)
        for item in value.values():
            for record_id, record in index_by_id(item).items():
                add(record_id, record)
    elif isinstance(value, list):
        for item in value:
            for record_id, record in index_by_id(item).items():
                add(record_id, record)
    return found


def _measurements(value: Any, field: str | None = None) -> list[float]:
    """工具结果里的测量值：只取数值字段，保留正负号；字符串（日期、编号）和时长计数字段都不算。"""
    if isinstance(value, bool) or field in NON_MEASUREMENT_FIELDS:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for key, item in value.items() for n in _measurements(item, key)]
    if isinstance(value, list):
        return [n for item in value for n in _measurements(item, field)]
    return []


def _split_numbers(text: str) -> tuple[list[str], list[str]]:
    """（带单位或紧跟 z 的片段，其余文字去掉编号、日期等豁免项后剩下的数字）。"""
    pieces = _MEASURED.findall(text)
    rest = _MEASURED.sub(" ", text)
    for pattern in (_ID, _CODE, _DATETIME, _DURATION, _COUNT):
        rest = pattern.sub(" ", rest)
    return pieces, _NUMBER.findall(rest)


def measured_numbers(text: str) -> list[str]:
    """文本里的测量值，带正负号：先取带单位或紧跟 z 的数字，再从其余文字里去掉豁免项后取剩下的数字。"""
    pieces, rest = _split_numbers(text)
    return [n for piece in pieces for n in _NUMBER.findall(piece)] + rest


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def source_text(value: Any) -> str:
    """出处里的文字（知识文档正文、故障描述等），去掉空白；各字段之间用“|”隔开，免得两个字段拼出一个数。"""
    if isinstance(value, str):
        return _compact(value)
    if isinstance(value, dict):
        return "|".join(source_text(item) for item in value.values())
    if isinstance(value, list):
        return "|".join(source_text(item) for item in value)
    return ""


def _quoted(piece: str, original: str) -> bool:
    """带单位的片段连同单位在原文里一字不差出现，前面不能再有数字、小数点或负号（“5℃”不算“-85℃”的照抄）。"""
    return re.search(rf"(?<![\d.\-−]){re.escape(_compact(piece))}(?!\d)", original) is not None


def untraceable_numbers(text: str, payload: Any, original: str = "") -> list[str]:
    """text 里在 payload 中找不到的测量值。允许照抄后四舍五入（3.86 → 3.9），正负号要一致，不允许自己算出新数。

    original 是所引出处的原文（只有 evidence 才传）：带单位的数字连同单位在原文里一字不差出现，也算照抄（文档写“85℃”）；
    不带单位的数字不能从文字里借（文档里的“90”证明不了“90 mm/s”）。
    """
    candidates = _measurements(payload)
    pieces, rest = _split_numbers(text)
    numbers = [n for piece in pieces if not (original and _quoted(piece, original)) for n in _NUMBER.findall(piece)]
    missing = []
    for raw in numbers + rest:
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value, half_step = float(raw.replace("−", "-")), 0.5 * 10 ** -decimals + 1e-9  # 2.15 写成 2.1 或 2.2 都算照抄
        if not any(abs(c - value) <= half_step for c in candidates):
            missing.append(raw)
    return missing


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _id_list(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(_text(i) for i in value)


TEXT, ID_LIST = (_text, "非空文字"), (_id_list, "非空的 id 列表")
ITEM_FIELDS = {
    "evidence": {"text": TEXT, "source": TEXT},
    "suspected_parts": {"part": TEXT, "status": TEXT, "reason": TEXT, "evidence": ID_LIST},
    "ruled_out": {"part": TEXT, "reason": TEXT, "evidence": ID_LIST},
    "actions": {"action": TEXT, "risk": TEXT},
}


def _shape_problems(report: Any) -> list[str]:
    """先查结构：每层字段齐全、类型对、必填内容不空。结构不对就不往下查，免得拿列表当 id 去查而崩溃。"""
    if not isinstance(report, dict):
        return ["输出不是 JSON 对象"]
    problems = [f"缺少字段 {key} 或类型不对（应为 {kind.__name__}）"
                for key, kind in REQUIRED_FIELDS.items() if not isinstance(report.get(key), kind)]
    if problems:
        return problems
    problems += [f"{key} 不能为空" for key in ("event_id", "conclusion", "status") if not _text(report[key])]
    for key, fields in ITEM_FIELDS.items():
        for i, item in enumerate(report[key]):
            if not isinstance(item, dict):
                problems.append(f"{key}[{i}] 要是 JSON 对象")
                continue
            problems += [f"{key}[{i}].{name} 要是{label}"
                         for name, (ok, label) in fields.items() if not ok(item.get(name))]
    problems += [f"missing_evidence[{i}] 要是非空文字" for i, item in enumerate(report["missing_evidence"])
                 if not _text(item)]
    return problems


def validate_report(report: Any, event_id: str, seen: dict[str, dict]) -> list[str]:
    """返回违反契约的问题清单；空列表表示通过。seen 是本次工具结果按 id 收起来的对象。"""
    problems = _shape_problems(report)
    if problems:
        return problems

    if report["event_id"] != event_id or event_id not in seen:
        problems.append(f"event_id 必须是本次调查并查过的事件 {event_id}")

    status = report["status"]
    if status == INCOMPLETE:
        problems.append("“调查未完成”只能由程序设置，请从四种状态里选")
    elif status not in STATUSES:
        problems.append(f"status 不合法：{status}，只能是 {' / '.join(STATUSES)}")

    if not report["evidence"]:
        problems.append("evidence 不能为空")
    for i, item in enumerate(report["evidence"]):
        if item["source"] not in seen:
            problems.append(f"evidence[{i}] 的出处 {item['source']} 不在本次工具结果里")
            continue
        source = seen[item["source"]]
        for number in untraceable_numbers(item["text"], source, source_text(source)):
            problems.append(f"evidence[{i}] 里的数字 {number} 在出处 {item['source']} 里找不到，不要自己计算")

    event = seen.get(event_id, {})
    key_parts = seen.get(event.get("equipment_id"), {}).get("key_parts")
    for i, part in enumerate(report["suspected_parts"]):
        if part["status"] != "疑似":
            problems.append(f"suspected_parts[{i}] 必须标为“疑似”")
        if key_parts is None:
            problems.append("写疑似部件前要先查设备台账（get_equipment）")
        elif not any(k in part["part"] for k in key_parts):
            problems.append(f"suspected_parts[{i}] 的 {part['part']} 不在台账关键部件（{'、'.join(key_parts)}）里")

    # 知识库里没验证过的案例：evidence 里可以如实写“查到一条未验证的记录”，但不能用来肯定或排除根因
    unverified = [record_id for record_id, record in seen.items() if record.get("verified") is False]
    for key in ("suspected_parts", "ruled_out"):
        for i, item in enumerate(report[key]):
            problems += [f"{key}[{i}] 的证据 {source} 不在本次工具结果里"
                         for source in item["evidence"] if source not in seen]
            used = [c for c in unverified if c in item["evidence"] or c in item["part"] + item["reason"]]
            problems += [f"{key}[{i}] 引用了未验证的案例 {c}，不能用来肯定或排除根因" for c in used]
            historical = [c for c in item["evidence"] if seen.get(c, {}).get("kind") in NON_CAUSAL_KINDS]
            problems += [f"{key}[{i}] 不能用历史故障工单 {c} 证明本次根因；只能说明过去发生过类似问题" for c in historical]

    for i, action in enumerate(report["actions"]):
        if action["risk"] not in RISKS:
            problems.append(f"actions[{i}] 的 risk 只能是“低”或“高”")

    # 结论、理由、措施里的数字，对照本次全部工具结果的数值字段检查；文档原文里的数字要放进 evidence、以该文档为出处照抄
    free_text = [report["conclusion"], *report["missing_evidence"]]
    free_text += [x[k] for x in report["suspected_parts"] + report["ruled_out"] for k in ("part", "reason")]
    free_text += [x["action"] for x in report["actions"]]
    everything = list(seen.values())
    for number in sorted({n for text in free_text for n in untraceable_numbers(text, everything)}):
        problems.append(f"结论或措施里的数字 {number} 在本次工具结果里找不到，不要自己计算")

    if status == "建议处置" and (not report["suspected_parts"] or not report["actions"]):
        problems.append("“建议处置”至少要有 1 个疑似部件和 1 条措施")
    if status == "需补充证据" and not report["missing_evidence"]:
        problems.append("“需补充证据”要写明还缺什么资料")
    if status == "疑似误报" and report["suspected_parts"]:
        problems.append("“疑似误报”不应再列疑似部件")

    return problems


def work_order_allowed(result) -> bool:
    """只有契约通过且状态是“建议处置”，才能进入人工审批、生成工单。"""
    return result.report is not None and not result.problems and result.status == "建议处置"
