import unittest
from types import SimpleNamespace

from agent.contract import INCOMPLETE, index_by_id, measured_numbers, validate_report, work_order_allowed

EVENT = "EV-IM011-0920T23"
TOOL_RESULTS = [
    {"id": EVENT, "equipment_id": "EQ-IM-011", "hours_so_far": 16, "peak_z": 3.2, "peak_at": "2026-09-21 13:00"},
    {"id": "EQ-IM-011", "name": "注塑机", "key_parts": ["螺杆", "加热圈"]},
    {"id": "SIG-IM011-vib-24h-0921T14", "mean": 2.451, "baseline_mean": 1.308, "z": 2.15},
    {"equipment_id": "EQ-IM-011", "records": [{"id": "WO-2026-1004", "kind": "历史故障工单", "cause": "加热圈老化", "cost": 4668}]},
]
SEEN = index_by_id(TOOL_RESULTS)

VALID = {
    "event_id": EVENT,
    "conclusion": "注塑机振动持续升高，疑似加热圈或螺杆问题，建议加强监测并安排点检",
    "status": "建议处置",
    "evidence": [
        {"text": "09-20 23:00 起振动偏离，持续 16 小时，峰值 z=3.2", "source": EVENT},
        {"text": "近 24h 振动均值 2.451 mm/s，基线 1.308，偏离 2.15σ", "source": "SIG-IM011-vib-24h-0921T14"},
    ],
    "suspected_parts": [{"part": "加热圈", "status": "疑似", "reason": "当前事件持续偏离",
                         "evidence": [EVENT]}],
    "ruled_out": [],
    "actions": [{"action": "加强监测，安排点检", "risk": "低"}],
    "missing_evidence": ["维保记录"],
}


def report_with(**changes):
    return {**VALID, **changes}


class ContractTests(unittest.TestCase):
    def test_valid_report_passes(self):
        self.assertEqual(validate_report(VALID, EVENT, SEEN), [])

    def test_made_up_source_is_rejected(self):
        evidence = VALID["evidence"] + [{"text": "上月也坏过", "source": "WO-2026-9999"}]
        problems = validate_report(report_with(evidence=evidence), EVENT, SEEN)
        self.assertTrue(any("WO-2026-9999" in p for p in problems))

    def test_historical_failure_cannot_prove_current_root_cause(self):
        part = [{"part": "加热圈", "status": "疑似", "reason": "历史故障相同",
                 "evidence": ["WO-2026-1004"]}]
        problems = validate_report(report_with(suspected_parts=part), EVENT, SEEN)
        self.assertTrue(any("历史故障工单" in p for p in problems))

    def test_model_cannot_set_incomplete_or_unknown_status(self):
        self.assertTrue(validate_report(report_with(status=INCOMPLETE), EVENT, SEEN))
        self.assertTrue(validate_report(report_with(status="已确认故障"), EVENT, SEEN))

    def test_parts_must_be_suspected_and_from_equipment_master(self):
        not_suspected = [{**VALID["suspected_parts"][0], "status": "确认"}]
        invented = [{**VALID["suspected_parts"][0], "part": "主轴轴承"}]
        self.assertTrue(validate_report(report_with(suspected_parts=not_suspected), EVENT, SEEN))
        problems = validate_report(report_with(suspected_parts=invented), EVENT, SEEN)
        self.assertTrue(any("台账关键部件" in p for p in problems))

    def test_parts_need_equipment_lookup(self):
        seen = {k: v for k, v in SEEN.items() if k != "EQ-IM-011"}
        problems = validate_report(VALID, EVENT, seen)
        self.assertTrue(any("get_equipment" in p for p in problems))

    def test_computed_numbers_rejected_but_rounded_copies_pass(self):
        computed = [{"text": "振动均值比基线高 1.143 mm/s", "source": "SIG-IM011-vib-24h-0921T14"}]
        rounded = [{"text": "振动均值约 2.5，偏离 2.2σ", "source": "SIG-IM011-vib-24h-0921T14"}]
        problems = validate_report(report_with(evidence=computed), EVENT, SEEN)
        self.assertTrue(any("1.143" in p for p in problems))
        self.assertEqual(validate_report(report_with(evidence=rounded), EVENT, SEEN), [])

    def test_ids_dates_and_durations_are_not_checked_as_numbers(self):
        text = "EV-IM011-0920T23 从 2026-09-20 23:00 开始，已持续 16 小时（9 月 21 日 14 点查看，窗口 24h）"
        self.assertEqual(measured_numbers(text), [])

    def test_numbers_in_conclusion_checked_against_all_results(self):
        problems = validate_report(report_with(conclusion="振动高出基线 87%，疑似加热圈"), EVENT, SEEN)
        self.assertTrue(any("87" in p for p in problems))

    def test_wrong_nested_types_are_problems_not_crashes(self):
        cases = {
            "source 是列表": report_with(evidence=[{"text": "x", "source": []}]),
            "证据列表里是对象": report_with(suspected_parts=[{**VALID["suspected_parts"][0], "evidence": [{"id": EVENT}]}]),
            "evidence 项是字符串": report_with(evidence=[EVENT]),
            "缺事实文本": report_with(evidence=[{"source": EVENT}]),
            "缺措施正文": report_with(actions=[{"risk": "低"}]),
            "缺失项是空字符串": report_with(missing_evidence=[""]),
            "结论为空": report_with(conclusion=" "),
        }
        for name, report in cases.items():
            with self.subTest(name):
                self.assertTrue(validate_report(report, EVENT, SEEN))

    def test_numbers_cannot_be_borrowed_from_dates_ids_or_durations(self):
        for text, source in [("振动为 2026 mm/s", EVENT), ("振动为 21 mm/s", "SIG-IM011-vib-24h-0921T14"),
                             ("振动为 11 mm/s", EVENT), ("偏离 16σ", EVENT)]:
            with self.subTest(text):
                problems = validate_report(report_with(evidence=[{"text": text, "source": source}]), EVENT, SEEN)
                self.assertTrue(any("找不到" in p for p in problems))

    def test_ranges_and_z_prefix_are_still_checked(self):
        sig = "SIG-IM011-vib-24h-0921T14"
        for text, source in [("振动范围 90-95 mm/s", sig), ("范围 90-95", sig), ("峰值 z3.9", EVENT)]:
            with self.subTest(text):
                problems = validate_report(report_with(evidence=[{"text": text, "source": source}]), EVENT, SEEN)
                self.assertTrue(any("找不到" in p for p in problems))
        for text, source in [("峰值 z3.2", EVENT), ("偏离在 2.1~2.2σ 之间", sig)]:
            with self.subTest(text):
                self.assertEqual(validate_report(report_with(evidence=[{"text": text, "source": source}]), EVENT, SEEN), [])

    def test_sign_must_match(self):
        flipped = [{"text": "峰值 z=-3.2", "source": EVENT}]
        self.assertTrue(validate_report(report_with(evidence=flipped), EVENT, SEEN))

    def test_codes_counts_and_durations_still_pass(self):
        text = "产线 L03，16 个告警小时，近 24h 均值 2.451"
        evidence = [{"text": text, "source": "SIG-IM011-vib-24h-0921T14"}]
        self.assertEqual(validate_report(report_with(evidence=evidence), EVENT, SEEN), [])

    def test_status_rules(self):
        self.assertTrue(validate_report(report_with(actions=[]), EVENT, SEEN))
        self.assertTrue(validate_report(report_with(status="需补充证据", missing_evidence=[]), EVENT, SEEN))
        self.assertTrue(validate_report(report_with(status="疑似误报"), EVENT, SEEN))
        self.assertTrue(validate_report(report_with(actions=[{"action": "停机检修", "risk": "中"}]), EVENT, SEEN))

    def test_work_order_only_for_passed_disposal(self):
        def result(status, problems=()):
            return SimpleNamespace(status=status, report={}, problems=list(problems))
        self.assertTrue(work_order_allowed(result("建议处置")))
        self.assertFalse(work_order_allowed(result("需补充证据")))
        self.assertFalse(work_order_allowed(result(INCOMPLETE, ["工具出错"])))


# 知识库检索结果：分数只用来排序；文档正文里的数字是文字，不是数值字段
KB_SEEN = index_by_id(TOOL_RESULTS + [{"query": "加热圈 温度", "results": [
    {"id": "KB-IM-HEAT-01", "kind": "文档", "score": 3.7, "excerpt": "加热圈温度长期超过 85℃ 会加速老化，保养周期 90 天。"},
    {"id": "CASE-0003", "kind": "案例", "score": 2.1, "verified": False, "excerpt": "螺杆卡滞，根因待回填。"},
]}])


class KnowledgeEvidenceTests(unittest.TestCase):
    def check(self, **changes):
        return validate_report(report_with(**changes), EVENT, KB_SEEN)

    def test_unverified_case_cannot_rule_out_a_part(self):
        by_evidence = [{"part": "螺杆", "reason": "根据该案例排除螺杆故障", "evidence": ["CASE-0003"]}]
        by_name = [{"part": "螺杆", "reason": "CASE-0003 里螺杆不是原因", "evidence": [EVENT]}]
        for ruled_out in (by_evidence, by_name):
            self.assertTrue(any("未验证的案例" in p for p in self.check(ruled_out=ruled_out)))
        fact = VALID["evidence"] + [{"text": "知识库里有一条未验证的相似记录", "source": "CASE-0003"}]
        self.assertEqual(self.check(evidence=fact), [])  # 如实写“查到了”可以，只是不能拿它下结论

    def test_search_score_is_not_a_measurement(self):
        evidence = VALID["evidence"] + [{"text": "振动为 3.7 mm/s", "source": "KB-IM-HEAT-01"}]
        self.assertTrue(any("3.7" in p for p in self.check(evidence=evidence)))
        self.assertTrue(any("3.7" in p for p in self.check(conclusion="振动为 3.7 mm/s，疑似加热圈问题")))

    def test_document_numbers_can_be_quoted_with_unit(self):
        quoted = VALID["evidence"] + [{"text": "文档：加热圈温度长期超过 85 ℃ 会加速老化", "source": "KB-IM-HEAT-01"}]
        self.assertEqual(self.check(evidence=quoted), [])
        for text in ("文档：超过 5℃ 会老化", "文档：超过 90 mm/s 要停机", "文档：超过 85 mm/s 要停机"):
            evidence = VALID["evidence"] + [{"text": text, "source": "KB-IM-HEAT-01"}]
            self.assertTrue(self.check(evidence=evidence), text)
        # 原文里的数字只能在 evidence 里、以这篇文档为出处照抄：挪到结论里，或挂在别的出处下，都不行
        self.assertTrue(self.check(conclusion="温度超过 85℃，疑似加热圈问题"))
        self.assertTrue(self.check(evidence=VALID["evidence"] + [{"text": "温度 85℃", "source": EVENT}]))


if __name__ == "__main__":
    unittest.main()
