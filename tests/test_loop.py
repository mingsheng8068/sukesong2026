import json
import unittest

from quality_agent.contract import validate_report
from quality_agent.llm import LLMReply, ScriptedLLM, ToolCall
from quality_agent.loop import run_investigation
from quality_agent.mock import scripted_llm_for_demo
from quality_agent.tools import execute_tool

VALID_REPORT = {
    "problem": {"part": "x", "batch": "H100-0925B", "defect": "x", "claimed_qty": None, "source": "C001"},
    "facts": [{"text": "x", "source": "C001"}],
    "similar_cases": [],
    "hypotheses": [{"cause": "y", "kind": "发生原因", "status": "疑似", "evidence": ["C001"]}],
    "containment": [],
    "reference_actions": [],
    "missing_evidence": ["x"],
    "status": "需补充证据",
}


def report_with(**changes):
    return {**VALID_REPORT, **changes}


class LoopTests(unittest.TestCase):
    def test_demo_run_passes_contract(self):
        result = run_investigation("C001", scripted_llm_for_demo("C001"))
        self.assertEqual(result.problems, [])
        self.assertEqual(result.turns, 6)
        self.assertEqual(result.report["status"], "需补充证据")

    def test_loop_stops_at_max_turns(self):
        always_tool = LLMReply(None, [ToolCall("c", "get_complaint", '{"complaint_id": "C001"}')])
        result = run_investigation("C001", ScriptedLLM([always_tool] * 3), max_turns=3)
        self.assertIsNone(result.report)
        self.assertIn("最大轮数", result.problems[0])

    def test_format_error_gets_one_retry(self):
        steps = [
            LLMReply(None, [ToolCall("c", "get_complaint", '{"complaint_id": "C001"}')]),
            LLMReply("这不是 JSON"),
            LLMReply(json.dumps(VALID_REPORT, ensure_ascii=False)),
        ]
        result = run_investigation("C001", ScriptedLLM(steps))
        self.assertEqual(result.problems, [])
        self.assertEqual(result.turns, 3)


class ToolTests(unittest.TestCase):
    def test_bad_calls_return_error_not_raise(self):
        self.assertIn("error", execute_tool("no_such_tool", "{}"))
        self.assertIn("error", execute_tool("get_complaint", "not json"))
        self.assertIn("error", execute_tool("get_complaint", '{"wrong_arg": 1}'))

    def test_missing_batch_is_a_result_not_an_error(self):
        result = execute_tool("get_lot", '{"batch": "H100-0930C"}')
        self.assertNotIn("error", result)
        self.assertFalse(result["found"])

    def test_typo_batch_gets_suggestion(self):
        result = execute_tool("get_lot", '{"batch": "H1OO-0918A"}')
        self.assertIn("H100-0918A", result["similar_batches"])

    def test_empty_records_are_returned(self):
        self.assertEqual(execute_tool("get_inspections", '{"batch": "H350-0915A"}')["records"], [])
        self.assertTrue(execute_tool("get_process_records", '{"batch": "H350-0915A"}')["note"])


class ContractTests(unittest.TestCase):
    def test_valid_report_passes(self):
        self.assertEqual(validate_report(VALID_REPORT, seen_ids={"C001"}), [])

    def test_source_must_come_from_tool_results(self):
        problems = validate_report(VALID_REPORT, seen_ids={"INS-H100-0925B-OQC"})
        self.assertTrue(any("不在本次工具结果里" in p for p in problems))

    def test_cause_must_be_suspected_with_kind(self):
        bad = report_with(hypotheses=[{"cause": "y", "kind": "原因", "status": "确认", "evidence": ["C001"]}])
        problems = validate_report(bad, seen_ids={"C001"})
        self.assertTrue(any("疑似" in p for p in problems))
        self.assertTrue(any("kind" in p for p in problems))

    def test_unverified_case_action_cannot_be_marked_verified(self):
        bad = report_with(reference_actions=[{"action": "清洗水路", "from_case": "CASE-058", "verified": True}])
        problems = validate_report(bad, seen_ids={"C001", "CASE-058"}, case_effective={"CASE-058": None})
        self.assertTrue(any("verified" in p for p in problems))

    def test_cannot_submit_with_missing_evidence(self):
        bad = report_with(status="可提交审批")
        problems = validate_report(bad, seen_ids={"C001"})
        self.assertTrue(any("可提交审批" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
