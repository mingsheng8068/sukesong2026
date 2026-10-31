import json
import unittest

import pandas as pd

from agent.contract import INCOMPLETE, work_order_allowed
from agent.llm import LLMReply, ScriptedLLM, ToolCall
from agent.loop import run_investigation
from agent.mock import broken_toolbox, scripted_llm
from agent.toolbox import TOOL_SCHEMAS, Toolbox
from pdm.adapters import OFFICIAL_DIR

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
AS_OF = pd.Timestamp("2026-09-21 14:00")
EVENT = "EV-IM011-0920T23"


def fake_event(event_id: str, as_of: pd.Timestamp) -> dict:
    return {"id": event_id, "equipment_id": "EQ-IM-011", "peak_z": 3.2, "seen_as_of": f"{as_of:%Y-%m-%d %H:%M}"}


def fake_equipment(equipment_id: str) -> dict:
    return {"id": equipment_id, "name": "注塑机", "key_parts": ["螺杆", "加热圈"]}


def broken(equipment_id: str, as_of: pd.Timestamp) -> dict:
    raise OSError("数据读不到")


FAKE_TOOLS = {"get_event": fake_event, "get_equipment": fake_equipment, "get_failure_history": broken}

REPORT = {
    "event_id": EVENT, "conclusion": "疑似加热圈问题，建议点检", "status": "建议处置",
    "evidence": [{"text": "峰值 z=3.2", "source": EVENT}],
    "suspected_parts": [{"part": "加热圈", "status": "疑似", "reason": "关键部件", "evidence": [EVENT]}],
    "ruled_out": [], "actions": [{"action": "安排点检", "risk": "低"}], "missing_evidence": [],
}


def call(name: str, **arguments) -> LLMReply:
    return LLMReply(None, [ToolCall(f"c_{name}", name, json.dumps(arguments))])


def answer(report: dict) -> LLMReply:
    return LLMReply(json.dumps(report, ensure_ascii=False))


LOOKUPS = [call("get_event", event_id=EVENT), call("get_equipment", equipment_id="EQ-IM-011")]


class ToolboxTests(unittest.TestCase):
    def test_schemas_never_mention_as_of(self):
        self.assertNotIn("as_of", json.dumps(TOOL_SCHEMAS))

    def test_model_supplied_as_of_is_dropped(self):
        result, note = Toolbox(AS_OF, FAKE_TOOLS).execute(
            "get_event", json.dumps({"event_id": EVENT, "as_of": "2026-09-30 23:00"}))
        self.assertEqual(result["seen_as_of"], "2026-09-21 14:00")
        self.assertEqual(note["dropped_as_of"], "2026-09-30 23:00")


class LoopTests(unittest.TestCase):
    def run_with(self, steps, tools=FAKE_TOOLS, max_turns=8):
        return run_investigation(EVENT, AS_OF, ScriptedLLM(steps), Toolbox(AS_OF, tools), max_turns=max_turns)

    def test_passing_report_allows_work_order(self):
        result = self.run_with(LOOKUPS + [answer(REPORT)])
        self.assertEqual(result.problems, [])
        self.assertEqual(result.status, "建议处置")
        self.assertTrue(work_order_allowed(result))

    def test_result_is_bound_to_event_and_as_of(self):
        result = self.run_with(LOOKUPS + [answer(REPORT)])
        self.assertEqual((result.event_id, result.as_of), (EVENT, AS_OF))
        failed = self.run_with([call("get_event", event_id=EVENT)] * 2, max_turns=2)
        self.assertEqual((failed.event_id, failed.as_of), (EVENT, AS_OF))

    def test_purpose_is_recorded_only_when_model_wrote_one(self):
        with_purpose = LLMReply("先看事件本身", [ToolCall("c1", "get_event", json.dumps({"event_id": EVENT}))])
        result = self.run_with([with_purpose, LOOKUPS[1], answer(REPORT)])
        self.assertEqual(result.trace[0]["purpose"], "先看事件本身")
        self.assertIsNone(result.trace[1]["purpose"])  # 没写就留空，不替模型补

    def test_other_statuses_do_not_allow_work_order(self):
        report = {**REPORT, "status": "需补充证据", "suspected_parts": [], "actions": [], "missing_evidence": ["维保记录"]}
        result = self.run_with(LOOKUPS + [answer(report)])
        self.assertEqual(result.status, "需补充证据")
        self.assertFalse(work_order_allowed(result))

    def test_tool_failure_stops_as_incomplete(self):
        result = self.run_with(LOOKUPS + [call("get_failure_history", equipment_id="EQ-IM-011"), answer(REPORT)])
        self.assertEqual(result.status, INCOMPLETE)
        self.assertIsNone(result.report)
        self.assertIn("数据读不到", result.error)
        self.assertEqual(len(result.trace), 3)  # 已查到的证据保留
        self.assertFalse(work_order_allowed(result))

    def test_contract_failing_twice_is_incomplete(self):
        fake = {**REPORT, "evidence": [{"text": "x", "source": "WO-2026-9999"}]}
        result = self.run_with(LOOKUPS + [answer(fake), answer(fake)])
        self.assertEqual(result.status, INCOMPLETE)
        self.assertTrue(result.problems)
        self.assertFalse(work_order_allowed(result))

    def test_fixed_after_one_retry_passes(self):
        fake = {**REPORT, "evidence": [{"text": "x", "source": "WO-2026-9999"}]}
        result = self.run_with(LOOKUPS + [answer(fake), answer(REPORT)])
        self.assertEqual(result.status, "建议处置")

    def test_too_many_turns_is_incomplete(self):
        result = self.run_with([call("get_event", event_id=EVENT)] * 3, max_turns=3)
        self.assertEqual(result.status, INCOMPLETE)
        self.assertFalse(work_order_allowed(result))

    def test_argument_error_goes_back_to_model(self):
        result = self.run_with([call("get_event")] + LOOKUPS + [answer(REPORT)])
        self.assertIn("error", result.trace[0]["result"])
        self.assertEqual(result.status, "建议处置")

    def test_malformed_report_retries_instead_of_crashing(self):
        malformed = {**REPORT, "evidence": [{"text": "x", "source": []}]}
        failed = self.run_with(LOOKUPS + [answer(malformed), answer(malformed)])
        self.assertEqual(failed.status, INCOMPLETE)
        fixed = self.run_with(LOOKUPS + [answer(malformed), answer(REPORT)])
        self.assertEqual(fixed.status, "建议处置")

    def test_model_api_failure_is_incomplete(self):
        def explode(messages):
            raise TimeoutError("接口超时")
        result = self.run_with([explode])
        self.assertEqual(result.status, INCOMPLETE)


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class ScriptedRunsOnOfficialData(unittest.TestCase):
    def test_demo_event_passes_contract(self):
        result = run_investigation(EVENT, AS_OF, scripted_llm(EVENT, "ok"))
        self.assertEqual(result.problems, [])
        self.assertEqual(result.status, "建议处置")

    def test_spike_is_suspected_false_alarm(self):
        as_of = pd.Timestamp("2026-09-21 04:00")
        result = run_investigation("EV-AGV031-0921T03", as_of, scripted_llm("EV-AGV031-0921T03", "ok"))
        self.assertEqual(result.problems, [])
        self.assertEqual(result.status, "疑似误报")

    def test_future_as_of_from_model_does_not_leak_failure_orders(self):
        result = run_investigation(EVENT, AS_OF, scripted_llm(EVENT, "future_as_of"))
        history = next(step for step in result.trace if step["tool"] == "get_failure_history")
        ids = [r["id"] for r in history["result"]["records"]]
        self.assertEqual(ids, ["WO-2026-1004"])  # 09-22 那张 WO-2026-1005 看不到
        self.assertIn("dropped_as_of", history)

    def test_broken_data_source_is_incomplete(self):
        result = run_investigation(EVENT, AS_OF, scripted_llm(EVENT, "tool_error"), broken_toolbox(AS_OF))
        self.assertEqual(result.status, INCOMPLETE)
        self.assertFalse(work_order_allowed(result))


if __name__ == "__main__":
    unittest.main()
