import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from pdm.adapters import OFFICIAL_DIR
from pdm.workorders import (
    CLOSED_FALSE_ALARM, FALSE_ALARM_LABEL, WORK_ORDER_LABEL, DecisionError, Store, approve, binding_problem,
    close_false_alarm, new_investigation, reject,
)

HAS_OFFICIAL = (OFFICIAL_DIR / "05_生产工单.csv").exists()
EVENT = "EV-IM011-0920T23"
AS_OF = pd.Timestamp("2026-09-21 14:00")
MODEL = "离线脚本模型"
ACTIONS = [{"action": "加强监测，安排点检", "risk": "低"}]
REPORT = {
    "event_id": EVENT, "conclusion": "注塑机振动持续升高，疑似加热圈问题", "status": "建议处置",
    "evidence": [{"text": "峰值 z=3.2", "source": EVENT}],
    "suspected_parts": [{"part": "加热圈", "status": "疑似", "reason": "关键部件", "evidence": [EVENT, "EQ-IM-011"]}],
    "ruled_out": [], "actions": ACTIONS, "missing_evidence": [],
}
TRACE = [
    {"turn": 1, "purpose": "先看事件", "tool": "get_event", "arguments": "{}",
     "result": {"id": EVENT, "equipment_id": "EQ-IM-011", "peak_z": 3.2}},
    {"turn": 2, "purpose": None, "tool": "get_equipment", "arguments": "{}",
     "result": {"id": "EQ-IM-011", "name": "注塑机", "line": "L03", "key_parts": ["螺杆", "加热圈"]}},
]


def investigation(status="建议处置", report=REPORT, problems=()):
    result = SimpleNamespace(status=status, report=report, problems=list(problems), turns=3, trace=TRACE,
                             error=None, event_id=EVENT, as_of=AS_OF)
    return new_investigation(result, MODEL)


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(self.dir.name)
        self.inv = investigation()
        self.store.save_investigation(self.inv)

    def tearDown(self):
        self.dir.cleanup()

    def approve(self, inv=None, as_of=AS_OF, model=MODEL, actions=ACTIONS, store=None):
        return approve(store or self.store, inv or self.inv, EVENT, as_of, model, actions, "")

    def test_approval_builds_work_order_from_report_and_snapshot(self):
        decision = self.approve()
        order = decision["work_order"]
        self.assertEqual(order["id"], "MWO-0921-01")
        self.assertEqual(order["description"], REPORT["conclusion"])
        self.assertEqual(order["evidence"], REPORT["evidence"])
        self.assertEqual((order["equipment_name"], order["line"]), ("注塑机", "L03"))  # 来自工具结果快照
        self.assertEqual(order["label"], WORK_ORDER_LABEL)
        self.assertTrue(order["possible_orders"])
        self.assertEqual(decision["investigation_as_of"], "2026-09-21 14:00")
        self.assertNotEqual(decision["decided_at"][:16], decision["investigation_as_of"])  # 实际审批时间另记
        self.assertIn(EVENT, decision["evidence"])

    def test_only_passed_disposal_reports_can_be_approved(self):
        for inv in (investigation(status="需补充证据"), investigation(problems=["出处不在工具结果里"])):
            with self.subTest(inv["status"]):
                with self.assertRaises(DecisionError):
                    self.approve(inv)

    def test_report_bound_to_event_time_and_model(self):
        self.assertIsNone(binding_problem(self.inv, EVENT, AS_OF, MODEL))
        for as_of, model in ((pd.Timestamp("2026-09-21 05:00"), MODEL), (AS_OF, "真实模型（x）")):
            with self.subTest(as_of=as_of, model=model):
                with self.assertRaises(DecisionError):
                    self.approve(as_of=as_of, model=model)
        with self.assertRaises(DecisionError):
            approve(self.store, self.inv, "EV-OTHER", AS_OF, MODEL, ACTIONS)

    def test_second_submission_is_rejected_even_after_restart(self):
        self.approve()
        with self.assertRaises(DecisionError):
            self.approve()
        with self.assertRaises(DecisionError):
            self.approve(store=Store(self.dir.name))  # 新建实例，模拟重启
        with self.assertRaises(DecisionError):
            reject(self.store, self.inv, EVENT, AS_OF, MODEL, "不同意")
        self.assertEqual(len(self.store.list_decisions()), 1)

    def test_write_failure_is_not_reported_as_success(self):
        from unittest import mock
        folder = Path(self.dir.name) / "decisions"
        with mock.patch.object(self.store, "_write_once", side_effect=PermissionError("只读目录")):
            with self.assertRaises(OSError):
                self.approve()
        self.assertTrue(not folder.exists() or list(folder.iterdir()) == [])  # 不留半个文件
        self.assertIsNone(self.store.load_decision(self.inv["id"]))

    def test_edited_action_needs_risk_confirmed_again(self):
        edited = [{"action": "停机检修加热圈", "risk": "低"}]
        with self.assertRaises(DecisionError):
            self.approve(actions=edited)
        decision = self.approve(actions=[{"action": "停机检修加热圈", "risk": "高", "risk_confirmed": True}])
        self.assertTrue(decision["modified"])
        self.assertEqual(decision["original_actions"], ACTIONS)
        self.assertEqual(decision["approved_actions"], [{"action": "停机检修加热圈", "risk": "高"}])

    def test_empty_inputs_are_rejected(self):
        for actions in ([], [{"action": " ", "risk": "低", "risk_confirmed": True}]):
            with self.subTest(actions=actions):
                with self.assertRaises(DecisionError):
                    self.approve(actions=actions)
        with self.assertRaises(DecisionError):
            reject(self.store, self.inv, EVENT, AS_OF, MODEL, "  ")
        self.assertEqual(self.store.list_decisions(), [])

    def test_false_alarm_close_needs_reason_and_is_not_a_verified_case(self):
        report = {**REPORT, "status": "疑似误报", "suspected_parts": [], "actions": []}
        inv = investigation(status="疑似误报", report=report)
        with self.assertRaises(DecisionError):
            close_false_alarm(self.store, inv, EVENT, AS_OF, MODEL, "")
        decision = close_false_alarm(self.store, inv, EVENT, AS_OF, MODEL, "现场确认传感器接线松动")
        self.assertEqual(decision["decision"], CLOSED_FALSE_ALARM)
        self.assertEqual(decision["label"], FALSE_ALARM_LABEL)
        self.assertNotIn("work_order", decision)

    def test_spare_parts_come_from_code_lookup(self):
        from unittest import mock
        from pdm import adapters
        empty = Path(self.dir.name) / "no-synthetic"
        with mock.patch.object(adapters, "SYNTHETIC_DIR", empty):
            self.assertEqual(self.approve()["work_order"]["spare_parts"], "备件表还没接入，待补")
        other = investigation()
        self.store.save_investigation(other)
        fixtures = Path(__file__).resolve().parent / "fixtures" / "synthetic"
        with mock.patch.object(adapters, "SYNTHETIC_DIR", fixtures):
            parts = self.approve(inv=other)["work_order"]["spare_parts"]
        self.assertEqual([p["id"] for p in parts], ["SP-T01"])  # 疑似部位“加热圈”对应的备件

    def test_work_order_numbers_do_not_repeat(self):
        other = investigation()
        self.store.save_investigation(other)
        first = self.approve()["work_order"]["id"]
        second = self.approve(inv=other)["work_order"]["id"]
        self.assertEqual((first, second), ("MWO-0921-01", "MWO-0921-02"))

    def test_concurrent_approvals_reserve_distinct_work_order_numbers(self):
        other = investigation()
        self.store.save_investigation(other)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda inv: self.approve(inv=inv), (self.inv, other)))
        self.assertEqual(sorted(r["work_order"]["id"] for r in results), ["MWO-0921-01", "MWO-0921-02"])


if __name__ == "__main__":
    unittest.main()
