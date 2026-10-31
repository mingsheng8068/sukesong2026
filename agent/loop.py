"""agent 循环：模型决定下一步查什么，代码负责执行、记录和检查。

一次只调查一个事件。和赛前骨架的循环写法相同（D25），多了程序设置的“调查未完成”：
工具内部出错、模型调用失败、超过最大轮数、契约第二次仍不通过，都停在这里，保留已查到的证据，交给人。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from .contract import INCOMPLETE, index_by_id, validate_report
from .prompts import SYSTEM_PROMPT
from .toolbox import Toolbox, ToolFailure

MAX_TURNS = 8
MAX_FORMAT_RETRIES = 1


@dataclass
class InvestigationResult:
    status: str               # 契约通过时取报告里的状态，否则为“调查未完成”
    report: dict | None
    problems: list[str]
    turns: int
    trace: list[dict] = field(default_factory=list)
    error: str | None = None
    event_id: str = ""                  # 调查的是哪个事件、站在哪个时点：页面据此判断报告是否过期
    as_of: pd.Timestamp | None = None


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


def run_investigation(event_id: str, as_of: pd.Timestamp, llm, toolbox: Toolbox | None = None,
                      max_turns: int = MAX_TURNS) -> InvestigationResult:
    toolbox = toolbox or Toolbox(as_of)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"请调查事件 {event_id}。调查时点：{toolbox.as_of:%Y-%m-%d %H:%M}。"},
    ]
    results: list[dict] = []  # 本次全部工具结果；按 id 建索引时同一 id 合并字段（见 contract.index_by_id）
    trace: list[dict] = []
    retries = 0

    def finish(status: str, report: dict | None, problems: list[str], turn: int, error: str | None = None):
        return InvestigationResult(status, report, problems, turn, trace, error, event_id, toolbox.as_of)

    for turn in range(1, max_turns + 1):
        try:
            reply = llm.chat(messages, toolbox.schemas)
        except Exception as exc:  # 模型接口出错（网络、额度）：不重试，交给人
            return finish(INCOMPLETE, None, [f"模型调用失败：{exc}"], turn, str(exc))

        # 模型要用工具：执行，把结果（包括参数错误）追加进上下文，进入下一轮
        if reply.tool_calls:
            purpose = (reply.content or "").strip() or None  # 模型调工具时顺带写的查询目的；没写就留空，不替它补
            messages.append({
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in reply.tool_calls
                ],
            })
            for call in reply.tool_calls:
                try:
                    result, note = toolbox.execute(call.name, call.arguments)
                except ToolFailure as exc:
                    trace.append({"turn": turn, "purpose": purpose, "tool": call.name, "arguments": call.arguments,
                                  "error": str(exc)})
                    return finish(INCOMPLETE, None, [str(exc)], turn, str(exc))
                results.append(result)
                trace.append({"turn": turn, "purpose": purpose, "tool": call.name, "arguments": call.arguments,
                              "result": result, **note})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue

        # 模型不再要工具：按契约检查，不通过就让它改一次
        report = parse_report(reply.content)
        try:
            problems = ["输出不是合法 JSON"] if report is None else validate_report(report, event_id, index_by_id(results))
        except Exception as exc:  # 契约本身出 bug 也不能让调查崩掉：按没通过处理
            problems = [f"契约检查出错：{type(exc).__name__}: {exc}"]
        if not problems:
            return finish(report["status"], report, [], turn)
        if retries < MAX_FORMAT_RETRIES:
            retries += 1
            messages.append({"role": "assistant", "content": reply.content or ""})
            messages.append({
                "role": "user",
                "content": "输出没通过检查：" + "；".join(problems) + "。请修正后只输出 JSON。",
            })
            continue
        return finish(INCOMPLETE, report, problems, turn)

    return finish(INCOMPLETE, None, [f"超过最大轮数 {max_turns}，没有得到结论"], max_turns)
