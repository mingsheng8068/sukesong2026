"""给模型用的工具箱：工具说明 + 执行。调查时点 as_of 在这里统一绑定（D24）。

模型看到的工具说明里没有 as_of，所以它没法填一个未来时间去偷看故障单；
就算它在参数里自己写了 as_of，也会被丢弃，并记进调查过程。

错误分两类：
- 参数不是 JSON、缺参数、未知工具：回传 {"error": ...}，模型可以改正后再调；
- 工具内部异常（数据读不到、代码 bug）：抛 ToolFailure，循环直接停下，状态记为“调查未完成”。
"""

from __future__ import annotations

import inspect
import json
from typing import Callable

import pandas as pd

from pdm import knowledge, tools


class ToolFailure(Exception):
    """工具内部出错，调查没法继续。"""


TOOL_FUNCTIONS: dict[str, Callable[..., dict]] = {
    "get_event": tools.get_event,
    "get_equipment": tools.get_equipment,
    "signal_summary": tools.signal_summary,
    "get_failure_history": tools.get_failure_history,
    "get_alarm_history": tools.get_alarm_history,
    "get_maintenance": tools.get_maintenance,
    "search_knowledge": knowledge.search_knowledge,
    "check_spare_parts": tools.check_spare_parts,
    "get_affected_orders": tools.get_affected_orders,
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_EQUIPMENT = {"equipment_id": {"type": "string", "description": "设备编号，如 EQ-IM-011"}}

TOOL_SCHEMAS = [
    _schema("get_event", "查事件：设备、开始时间、到目前持续多久、峰值偏离几个 σ、是否还在进行，以及同时段温度和电流的变化。调查的第一步。",
            {"event_id": {"type": "string", "description": "事件编号，如 EV-IM011-0920T23"}}, ["event_id"]),
    _schema("get_equipment", "查设备台账：名称、产线、投产日期、维保周期、关键部件。写疑似部件前必须先查，疑似部件只能从关键部件里选。",
            _EQUIPMENT, ["equipment_id"]),
    _schema("signal_summary", "查某个信号最近一段时间的摘要：均值、基线、偏离几个 σ、趋势、缺失几小时。只有振动、温度、电流三个信号可查。",
            {**_EQUIPMENT,
             "signal": {"type": "string", "enum": list(tools.AGENT_SIGNALS),
                        "description": "vib = 主轴振动RMS，temp = 主轴温度，current = 主轴电流"},
             "hours": {"type": "integer", "description": "看最近多少小时，默认 24，最多 168"}},
            ["equipment_id", "signal"]),
    _schema("get_failure_history", "查这台设备已经修完的历史故障维修单：现象、原因、停机时长、费用、处理方式。只说明过去出过什么问题，不能证明本次是同一原因。",
            _EQUIPMENT, ["equipment_id"]),
    _schema("get_alarm_history", "查这台设备最近几天出过的异常事件（本系统生成的异常履历），看是反复出现还是第一次。",
            {**_EQUIPMENT, "days": {"type": "integer", "description": "往前看几天，默认 7，最多 30"}},
            ["equipment_id"]),
    _schema("get_maintenance", "查这台设备最近做完的保养记录，以及已经排好的下次保养日期。刚保养过、或下次保养就在几天后，都会影响判断和建议。",
            {**_EQUIPMENT, "days": {"type": "integer", "description": "往前看几天，默认 60"}}, ["equipment_id"]),
    _schema("search_knowledge", "按现象检索设备维修知识文档和历史案例。相似只说明描述相近；verified 为 false 的案例不能用来肯定或排除部件；score 只是检索排序分，不是测量值。",
            {"query": {"type": "string", "description": "设备类型 + 部件 + 现象，如“注塑机 加热圈 温度升高”"},
             "top_k": {"type": "integer", "description": "返回几条，默认 3，最多 5"}}, ["query"]),
    _schema("check_spare_parts", "查某个部件对应的备件：库存、安全库存、采购周期。建议换件或检修时用。",
            {"component": {"type": "string", "description": "台账关键部件，如“加热圈”"}}, ["component"]),
    _schema("get_affected_orders", "查可以在这条产线生产的订单（客户等级、交期）。不是实际排产，结论只能写“可能受影响”。",
            {"line": {"type": "string", "description": "产线编号，从 get_equipment 的 line 字段取，如 L03"}}, ["line"]),
]


class Toolbox:
    schemas = TOOL_SCHEMAS

    def __init__(self, as_of: pd.Timestamp, functions: dict[str, Callable[..., dict]] | None = None):
        self.as_of = pd.Timestamp(as_of)
        self.functions = functions or TOOL_FUNCTIONS

    def execute(self, name: str, raw_arguments: str | None) -> tuple[dict, dict]:
        """返回 (工具结果, 给调查过程的备注)。"""
        function = self.functions.get(name)
        if function is None:
            return {"error": f"未知工具 {name}，可用工具：{', '.join(self.functions)}"}, {}
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {"error": "参数不是合法 JSON"}, {}
        if not isinstance(arguments, dict):
            return {"error": "参数要是 JSON 对象"}, {}

        note = {}
        if "as_of" in arguments:
            note["dropped_as_of"] = arguments.pop("as_of")  # 调查时点由程序决定，模型填的不算
        if "as_of" in inspect.signature(function).parameters:
            arguments["as_of"] = self.as_of
        try:
            inspect.signature(function).bind(**arguments)
        except TypeError as exc:
            return {"error": f"参数不对：{exc}"}, note

        try:
            return function(**arguments), note
        except Exception as exc:  # 工具内部出错：不让模型接着猜，交给循环转“调查未完成”
            raise ToolFailure(f"{name} 出错：{type(exc).__name__}: {exc}") from exc
