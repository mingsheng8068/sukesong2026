"""agent 循环：模型决定下一步，代码负责执行、记录和检查。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .contract import collect_case_effective, collect_ids, validate_report
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, execute_tool

MAX_TURNS = 8
MAX_FORMAT_RETRIES = 1


@dataclass
class InvestigationResult:
    report: dict | None
    problems: list[str]
    turns: int
    trace: list[dict] = field(default_factory=list)


def parse_report(content: str | None) -> dict | None:
    """从模型回复里取出 JSON 对象；兼容 ```json 代码块。"""
    if not content:
        return None
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None


def run_investigation(complaint_id: str, llm, max_turns: int = MAX_TURNS) -> InvestigationResult:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"请调查客诉 {complaint_id}。"},
    ]
    seen_ids: set[str] = set()
    case_effective: dict = {}
    trace: list[dict] = []
    retries = 0

    for turn in range(1, max_turns + 1):
        reply = llm.chat(messages, TOOL_SCHEMAS)

        # 模型要用工具：执行，把结果（包括错误）追加进上下文，进入下一轮
        if reply.tool_calls:
            messages.append({
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in reply.tool_calls
                ],
            })
            for call in reply.tool_calls:
                result = execute_tool(call.name, call.arguments)
                seen_ids |= collect_ids(result)
                case_effective.update(collect_case_effective(result))
                trace.append({"turn": turn, "tool": call.name, "arguments": call.arguments, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue

        # 模型不再要工具：按契约检查结论，不通过就让它改一次
        report = parse_report(reply.content)
        problems = ["输出不是合法 JSON"] if report is None else validate_report(report, seen_ids, case_effective)
        if problems and retries < MAX_FORMAT_RETRIES:
            retries += 1
            messages.append({"role": "assistant", "content": reply.content or ""})
            messages.append({
                "role": "user",
                "content": "输出没通过检查：" + "；".join(problems) + "。请修正后只输出 JSON。",
            })
            continue
        return InvestigationResult(report, problems, turn, trace)

    return InvestigationResult(None, [f"超过最大轮数 {max_turns}，没有得到结论"], max_turns, trace)
