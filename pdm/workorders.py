"""审批和工单：固定流程，纯代码，不调模型（DECISIONS D27、D28）。

一次调查 = 一条调查记录：绑定事件、调查时点、模型，并保存本次工具结果快照（点开出处读快照，不按新时刻重查）。
每条调查最多一个决定：通过（生成工单）/ 驳回 / 确认误报并关闭。决定和工单写在同一个文件里，
先写临时文件再用 os.link 原子地占用文件名：重复点击、刷新、重启后再提交，都只会成功一次；写一半失败不留残缺文件。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from agent.contract import RISKS, index_by_id, work_order_allowed
from .priority import possible_orders
from .tools import check_spare_parts

RUNTIME_DIR = Path(__file__).resolve().parent.parent / "data" / "runtime"
APPROVER = "值班工程师"   # D13：不做用户系统，审批人写死
TIME = "%Y-%m-%d %H:%M"
APPROVED, REJECTED, CLOSED_FALSE_ALARM = "通过", "驳回", "确认误报并关闭"
WORK_ORDER_LABEL = "人工批准的处置方案；根因仍为疑似，待维修后验证"
FALSE_ALARM_LABEL = "关闭事件 ≠ 确认传感器故障，不进入已验证案例"


class DecisionError(Exception):
    """输入不合法、报告和当前请求对不上、或已经有决定：拒绝，不写任何东西。"""


def _now() -> str:
    """实际时间按北京时间记（本机时钟可能不在东八区）。"""
    return pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")


def new_investigation(result, model_label: str) -> dict:
    """把一次调查结果存成记录：绑定事件、调查时点、模型，附上本次工具结果快照。"""
    seen = index_by_id([step["result"] for step in result.trace if "result" in step])
    return {
        "id": f"INV-{result.event_id}-{result.as_of:%m%dT%H}-{uuid.uuid4().hex[:6]}",
        "event_id": result.event_id, "as_of": f"{result.as_of:{TIME}}", "model": model_label,
        "started_at": _now(), "status": result.status, "report": result.report, "problems": result.problems,
        "turns": result.turns, "trace": result.trace, "error": result.error, "seen": seen,
    }


def binding_problem(investigation: dict, event_id: str, as_of: pd.Timestamp, model_label: str) -> str | None:
    """报告是否还对应当前的事件、时刻和模型；对不上就返回原因。"""
    if investigation["event_id"] != event_id:
        return f"这份报告调查的是 {investigation['event_id']}，不是当前事件"
    if investigation["as_of"] != f"{as_of:{TIME}}" or investigation["model"] != model_label:
        return (f"这份报告基于 {investigation['as_of']} / {investigation['model']}，"
                f"与当前时刻或模型不一致，已过期，请重新调查")
    return None


def _check(investigation: dict, event_id: str, as_of: pd.Timestamp, model_label: str) -> None:
    problem = binding_problem(investigation, event_id, as_of, model_label)
    if problem:
        raise DecisionError(problem)


def _allowed(investigation: dict) -> bool:
    return work_order_allowed(SimpleNamespace(**{k: investigation[k] for k in ("status", "report", "problems")}))


def _cited_evidence(investigation: dict) -> dict:
    report = investigation["report"] or {}
    sources = {item["source"] for item in report.get("evidence", [])}
    sources |= {s for part in report.get("suspected_parts", []) for s in part["evidence"]}
    return {s: investigation["seen"][s] for s in sorted(sources) if s in investigation["seen"]}


def _check_actions(original: list[dict], actions: list[dict]) -> bool:
    """校验人工确认后的措施；返回是否改过。改过或新增的措施必须重新确认风险等级，不沿用原值。"""
    if not actions:
        raise DecisionError("至少要有一条措施")
    unchanged = {(a["action"], a["risk"]) for a in original}
    modified = False
    for i, action in enumerate(actions, 1):
        text = str(action.get("action", "")).strip()
        if not text:
            raise DecisionError(f"第 {i} 条措施是空的")
        if action.get("risk") not in RISKS:
            raise DecisionError(f"第 {i} 条措施的风险等级只能是“低”或“高”")
        if (text, action["risk"]) not in unchanged:
            modified = True
            if not action.get("risk_confirmed"):
                raise DecisionError(f"第 {i} 条措施改过了，请重新确认它的风险等级")
    return modified or len(actions) != len(original)


def _spare_parts(suspected_parts: list[dict], as_of: pd.Timestamp) -> list[dict] | str:
    """按疑似部位查备件（代码查询，库存是当前快照）；备件表还没接入时写明待补。"""
    found = [check_spare_parts(p["part"], as_of) for p in suspected_parts]
    parts = [part for result in found for part in result.get("parts", [])]
    if parts:
        return parts
    return "备件表还没接入，待补" if any(r.get("found") is False for r in found) else "没有对应的备件记录"


def build_work_order(investigation: dict, actions: list[dict], note: str, number: str, decided_at: str) -> dict:
    """按固定模板生成工单：字段都来自已通过契约的报告、工具结果快照和代码查询，不调模型。"""
    report = investigation["report"]
    event = investigation["seen"][investigation["event_id"]]
    equipment = investigation["seen"].get(event["equipment_id"], {})
    line = equipment.get("line", "")
    orders = possible_orders(line, pd.Timestamp(investigation["as_of"])) if line else pd.DataFrame()
    return {
        "id": number, "label": WORK_ORDER_LABEL, "status": "待执行",
        "event_id": investigation["event_id"], "equipment_id": event["equipment_id"],
        "equipment_name": equipment.get("name", ""), "line": line,
        "description": report["conclusion"],
        "evidence": report["evidence"],
        "suspected_parts": [{"part": p["part"], "reason": p["reason"], "evidence": p["evidence"]}
                            for p in report["suspected_parts"]],
        "actions": [{"action": a["action"].strip(), "risk": a["risk"]} for a in actions],
        "possible_orders": [{"id": o["id"], "customer_level": o["customer_level"], "due": f"{o['due']:%Y-%m-%d}"}
                            for o in orders.to_dict("records")],
        "possible_orders_note": "可在这条产线生产的订单；官方数据没有订单创建时间，可知时间未知，只能说“可能受影响”",
        "spare_parts": _spare_parts(report["suspected_parts"], pd.Timestamp(investigation["as_of"])),
        "note": note.strip(),
        "approver": APPROVER, "approved_at": decided_at,
        "investigation_id": investigation["id"], "investigation_as_of": investigation["as_of"],
        "model": investigation["model"],
    }


class Store:
    """本地 JSON 存储：data/runtime/investigations/、data/runtime/decisions/（不进 git）。"""

    def __init__(self, root: Path | str = RUNTIME_DIR):
        self.root = Path(root)

    def _path(self, kind: str, record_id: str) -> Path:
        return self.root / kind / f"{record_id}.json"

    def _write_once(self, path: Path, record: dict) -> None:
        """原子、独占地写入：要么完整写好，要么什么都没留下；文件已存在就抛 FileExistsError。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.link(tmp, path)
        finally:
            os.unlink(tmp)

    def _read(self, path: Path) -> dict | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save_investigation(self, investigation: dict) -> None:
        self._write_once(self._path("investigations", investigation["id"]), investigation)

    def load_investigation(self, investigation_id: str) -> dict | None:
        return self._read(self._path("investigations", investigation_id))

    def load_decision(self, investigation_id: str) -> dict | None:
        return self._read(self._path("decisions", investigation_id))

    def list_decisions(self) -> list[dict]:
        folder = self.root / "decisions"
        records = [self._read(p) for p in folder.glob("*.json")] if folder.exists() else []
        return sorted(records, key=lambda r: r["decided_at"])

    def next_work_order_id(self, as_of: str) -> str:
        prefix = f"MWO-{pd.Timestamp(as_of):%m%d}-"
        taken = [r["work_order"]["id"] for r in self.list_decisions() if r.get("work_order")]
        allocation_dir = self.root / "work_order_ids"
        allocation_dir.mkdir(parents=True, exist_ok=True)
        while True:
            reserved = [p.stem for p in allocation_dir.glob(f"{prefix}*.lock")]
            used = set(taken) | set(reserved)
            number = 1
            while f"{prefix}{number:02d}" in used:
                number += 1
            work_order_id = f"{prefix}{number:02d}"
            lock = allocation_dir / f"{work_order_id}.lock"
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return work_order_id
            except FileExistsError:
                time.sleep(0.001)

    def save_decision(self, decision: dict) -> dict:
        """写入并读回；只有读回成功才算保存成功。"""
        path = self._path("decisions", decision["investigation_id"])
        try:
            self._write_once(path, decision)
        except FileExistsError:
            raise DecisionError("这次调查已经有决定了，不能重复提交") from None
        saved = self._read(path)
        if saved != decision:
            raise OSError(f"写入后读回不一致：{path}")
        return saved


def _decision(investigation: dict, kind: str, reason: str, decided_at: str) -> dict:
    return {
        "investigation_id": investigation["id"], "decision": kind, "reason": reason.strip(),
        "event_id": investigation["event_id"], "model": investigation["model"],
        "investigation_as_of": investigation["as_of"],   # 调查模拟时点
        "decided_at": decided_at,                          # 实际审批时间
        "approver": APPROVER,
        "report": investigation["report"], "evidence": _cited_evidence(investigation),
    }


def approve(store: Store, investigation: dict, event_id: str, as_of: pd.Timestamp, model_label: str,
            actions: list[dict], note: str = "") -> dict:
    _check(investigation, event_id, as_of, model_label)
    if not _allowed(investigation):
        raise DecisionError("只有契约通过、状态为“建议处置”的报告才能审批出工单")
    if store.load_decision(investigation["id"]):
        raise DecisionError("这次调查已经有决定了，不能重复提交")
    original = investigation["report"]["actions"]
    modified = _check_actions(original, actions)
    decided_at = _now()
    decision = _decision(investigation, APPROVED, note, decided_at)
    decision.update(original_actions=original, approved_actions=[{"action": a["action"].strip(), "risk": a["risk"]}
                                                                for a in actions], modified=modified)
    decision["work_order"] = build_work_order(investigation, actions, note,
                                              store.next_work_order_id(investigation["as_of"]), decided_at)
    return store.save_decision(decision)


def reject(store: Store, investigation: dict, event_id: str, as_of: pd.Timestamp, model_label: str,
           reason: str) -> dict:
    _check(investigation, event_id, as_of, model_label)
    if not _allowed(investigation):
        raise DecisionError("只有进入审批的报告才能驳回")
    if not reason.strip():
        raise DecisionError("驳回要写理由")
    return store.save_decision(_decision(investigation, REJECTED, reason, _now()))


def close_false_alarm(store: Store, investigation: dict, event_id: str, as_of: pd.Timestamp, model_label: str,
                      reason: str) -> dict:
    _check(investigation, event_id, as_of, model_label)
    if investigation["status"] != "疑似误报" or investigation["problems"]:
        raise DecisionError("只有契约通过、状态为“疑似误报”的报告才能确认误报并关闭")
    if not reason.strip():
        raise DecisionError("确认误报要写理由")
    decision = _decision(investigation, CLOSED_FALSE_ALARM, reason, _now())
    decision["label"] = FALSE_ALARM_LABEL
    return store.save_decision(decision)
