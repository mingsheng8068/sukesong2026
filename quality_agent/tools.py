"""agent 能调用的工具：查客诉、查批次、查检验记录、查工艺 / 设备记录、检索相似案例。

两类情况分开处理：
- 查无此数据（批次不存在等）是正常结果，返回 {"found": false, ...}，让模型写进 missing_evidence；
- 参数错、未知工具这类错误返回 {"error": ...}，不抛到循环外。
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Callable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _lot_ids() -> list[str]:
    return [lot["id"] for lot in _load("lots.json")]


def _batch_not_found(batch: str) -> dict:
    return {
        "found": False,
        "message": f"系统里没有批次 {batch}",
        "similar_batches": difflib.get_close_matches(batch, _lot_ids(), n=3, cutoff=0.6),
    }


def get_complaint(complaint_id: str) -> dict:
    for complaint in _load("complaints.json"):
        if complaint["id"] == complaint_id:
            return complaint
    return {"found": False, "message": f"没有客诉 {complaint_id}"}


def get_lot(batch: str) -> dict:
    for lot in _load("lots.json"):
        if lot["id"] == batch:
            return lot
    return _batch_not_found(batch)


def get_inspections(batch: str) -> dict:
    if batch not in _lot_ids():
        return _batch_not_found(batch)
    return {"batch": batch, "records": _load("inspections.json").get(batch, [])}


def get_process_records(batch: str) -> dict:
    if batch not in _lot_ids():
        return _batch_not_found(batch)
    entry = _load("process_records.json").get(batch, {"records": [], "note": "没有该批次的工艺 / 设备记录"})
    return {"batch": batch, **entry}


def search_similar_cases(query: str, top_k: int = 3) -> dict:
    # 占位实现：按字符重合打分。“刮痕”和“划伤”一个字都不重合，后续换成 BM25 + 语义检索。
    query_chars = set(query)
    scored = []
    for case in _load("cases.json"):
        text = case["part_type"] + case["symptom"]
        score = len(query_chars & set(text)) / max(len(query_chars), 1)
        if score > 0:
            scored.append((round(score, 2), case))
    scored.sort(key=lambda item: item[0], reverse=True)
    return {
        "query": query,
        "results": [{"similarity": score, **case} for score, case in scored[:top_k]],
        "note": "相似度只代表描述相近，不代表同一原因；effective 为 null 表示措施尚未验证",
    }


TOOLS: dict[str, Callable[..., dict]] = {
    "get_complaint": get_complaint,
    "get_lot": get_lot,
    "get_inspections": get_inspections,
    "get_process_records": get_process_records,
    "search_similar_cases": search_similar_cases,
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


_BATCH = {"batch": {"type": "string", "description": "批次号，如 H100-0925B"}}

TOOL_SCHEMAS = [
    _schema("get_complaint", "按编号查客诉或厂内异常的原文。调查的第一步，零件、批次、缺陷和数量要从原文里整理出来。",
            {"complaint_id": {"type": "string", "description": "编号，如 C001"}}, ["complaint_id"]),
    _schema("get_lot", "按批次号查批次信息：零件、生产日期、机台或产线、材料批次、包装、变更记录。查不到时会返回最接近的批次号。",
            _BATCH, ["batch"]),
    _schema("get_inspections", "按批次号查检验记录（来料、巡检、全检、出货检验、退回样品复检）。records 为空表示没有记录，应写进 missing_evidence。",
            _BATCH, ["batch"]),
    _schema("get_process_records", "按批次号查生产时段的工艺 / 设备记录（注塑参数、干燥、搬送设备振动等）。",
            _BATCH, ["batch"]),
    _schema("search_similar_cases", "按零件类型和不良现象检索历史已闭环案例。结果只说明描述相近，不能证明本批次是同一原因。",
            {"query": {"type": "string", "description": "零件类型 + 不良现象，如“注塑外壳 边角 划伤”"},
             "top_k": {"type": "integer", "description": "返回条数，默认 3"}}, ["query"]),
]


def execute_tool(name: str, raw_arguments: str | None) -> dict:
    """按名字分发工具；参数错、未知工具都变成 {"error": ...} 返回，不让循环崩掉。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"未知工具 {name}，可用工具：{', '.join(TOOLS)}"}
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "参数不是合法 JSON"}
    try:
        return tool(**arguments)
    except TypeError as exc:
        return {"error": f"参数不对：{exc}"}
