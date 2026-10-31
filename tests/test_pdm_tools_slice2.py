"""切片 2 的工具：异常履历、可能受影响工单（官方数据）；维保、备件、知识库（合成数据，用 tests/fixtures 夹具）。"""

import json
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from agent.contract import index_by_id, validate_report
from pdm import adapters, knowledge
from pdm.adapters import OFFICIAL_DIR
from pdm.tools import check_spare_parts, get_affected_orders, get_alarm_history, get_maintenance

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AS_OF = pd.Timestamp("2026-09-21 14:00")
T = pd.Timestamp


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class OfficialToolTests(unittest.TestCase):
    def test_alarm_history_only_sees_events_before_as_of(self):
        result = get_alarm_history("EQ-IM-011", AS_OF, days=30)
        ids = [r["id"] for r in result["records"]]
        self.assertIn("EV-IM011-0920T23", ids)
        self.assertIn("EV-IM011-0909T19", ids)
        self.assertTrue(all(T(r["start"]) <= AS_OF for r in result["records"]))
        later = get_alarm_history("EQ-IM-011", T("2026-09-30 12:00"), days=30)
        self.assertIn("EV-IM011-0924T08", [r["id"] for r in later["records"]])
        self.assertNotIn("EV-IM011-0924T08", ids)  # 9-24 的尖峰在 9-21 时看不到

    def test_alarm_history_counts_only_up_to_as_of(self):
        record = next(r for r in get_alarm_history("EQ-IM-011", AS_OF)["records"] if r["id"] == "EV-IM011-0920T23")
        self.assertEqual(record["alarm_hours"], 6)
        self.assertEqual(record["state"], "进行中")

    def test_affected_orders_are_only_possible(self):
        result = get_affected_orders("L03", AS_OF)
        self.assertEqual(result["known_at"], "未知")
        self.assertIn("可能受影响", result["note"])
        self.assertTrue(all(o["kind"] == "生产工单" for o in result["orders"]))
        self.assertFalse(get_affected_orders("L99", AS_OF)["found"])

    def test_event_id_listed_by_two_tools_keeps_all_fields(self):
        # 异常履历里也列着正在调查的事件：按 id 建索引时要合并，不能把 get_event 的 equipment_id 覆盖掉
        from pdm.tools import get_event
        seen = index_by_id([get_event("EV-IM011-0920T23", AS_OF), get_alarm_history("EQ-IM-011", AS_OF)])
        self.assertEqual(seen["EV-IM011-0920T23"]["equipment_id"], "EQ-IM-011")

    def test_results_are_json_serializable(self):
        for result in (get_alarm_history("EQ-IM-011", AS_OF), get_affected_orders("L03", AS_OF)):
            json.dumps(result, ensure_ascii=False)


class NotConnectedTests(unittest.TestCase):
    """合成数据还没放进来时：工具返回“还没接入”，不报错、不编。"""

    def test_missing_files_mean_not_connected(self):
        empty = FIXTURES / "does-not-exist"
        with mock.patch.object(adapters, "SYNTHETIC_DIR", empty), mock.patch.object(knowledge, "KB_DIR", empty):
            if HAS_OFFICIAL:
                self.assertFalse(get_maintenance("EQ-IM-011", AS_OF)["found"])
                self.assertFalse(check_spare_parts("加热圈", AS_OF)["found"])
            self.assertFalse(knowledge.search_knowledge("加热圈", AS_OF)["found"])
            self.assertTrue(adapters.load_criticality().empty)

    def test_wrong_header_is_a_data_error(self):
        with tempfile_dir() as folder:
            (folder / "maintenance.csv").write_text("record_id,equipment_id\nMT-1,EQ-IM-011\n", encoding="utf-8")
            with mock.patch.object(adapters, "SYNTHETIC_DIR", folder):
                with self.assertRaises(ValueError):
                    adapters.load_maintenance()


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class SyntheticToolTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(adapters, "SYNTHETIC_DIR", FIXTURES / "synthetic")
        patch.start()
        self.addCleanup(patch.stop)

    def test_maintenance_visible_only_after_it_ends(self):
        result = get_maintenance("EQ-IM-011", AS_OF, days=90)
        ids = [r["id"] for r in result["records"]]
        self.assertEqual(ids, ["MT-IM011-0910", "MT-IM011-0712"])  # 9-25 那条在调查时点之后
        self.assertEqual(result["last"]["id"], "MT-IM011-0910")
        self.assertEqual(result["last"]["days_since_last"], 11)
        self.assertEqual(result["next_planned"], {"date": "2026-11-09", "from": "MT-IM011-0910", "days_to_next": 48})

    def test_spare_parts_flag_below_safety_stock(self):
        parts = {p["id"]: p for p in check_spare_parts("加热圈", AS_OF)["parts"]}
        self.assertTrue(parts["SP-T01"]["below_safety_stock"])
        self.assertEqual(list(parts), ["SP-T01"])
        self.assertFalse(check_spare_parts("齿轮", AS_OF)["found"])

    def test_criticality_feeds_priority_when_present(self):
        from pdm.detection import events_as_of, official_detection
        from pdm.priority import rank_events
        table = rank_events(events_as_of(official_detection(), AS_OF), AS_OF)
        row = table[table["id"] == "EV-IM011-0920T23"].iloc[0]
        self.assertEqual((row["criticality"], row["factor"]), ("A", 1.5))


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(knowledge, "KB_DIR", FIXTURES / "kb")
        patch.start()
        self.addCleanup(patch.stop)

    def search(self, query, as_of=AS_OF):
        return knowledge.search_knowledge(query, as_of)["results"]

    def test_finds_matching_documents_and_cases(self):
        ids = [r["id"] for r in self.search("注塑机 加热圈 温度升高")]
        self.assertEqual(ids[0], "KB-TEST-01")
        self.assertIn("CASE-9001", ids)
        self.assertEqual([r["id"] for r in self.search("AGV 驱动轮 振动")], ["KB-TEST-02"])
        self.assertEqual(self.search("回流焊炉 风机"), [])

    def test_cases_created_after_as_of_are_hidden(self):
        self.assertNotIn("CASE-9002", [r["id"] for r in self.search("加热圈 温度升高")])
        later = [r["id"] for r in self.search("加热圈 温度升高", T("2026-09-30 00:00"))]
        self.assertIn("CASE-9002", later)

    def test_case_is_an_immutable_snapshot(self):
        # CASE-9004：9-01 建档时根因待查，9-25 才回填并审核。9-21 只能看到建档那一份，看不到后来写的根因
        early = {r["id"]: r for r in self.search("装配机器人 伺服 电流偏高")}["CASE-9004"]
        self.assertEqual((early["verified"], early["updated_at"]), (False, "2026-09-01 09:00"))
        self.assertNotIn("编码器", early["excerpt"])
        later = {r["id"]: r for r in self.search("装配机器人 伺服 电流偏高", T("2026-09-30 00:00"))}["CASE-9004"]
        self.assertTrue(later["verified"])
        self.assertIn("编码器", later["excerpt"])

    def test_case_written_after_as_of_is_hidden_entirely(self):
        # 只有一份快照：9-01 创建、9-25 才写定根因并验证 → 9-21 调查时整条看不到，不是只把 verified 改成 false
        with tempfile_dir() as folder:
            (folder / "cases").mkdir()
            source = (FIXTURES / "kb" / "cases" / "CASE-9004-v2.md").read_text(encoding="utf-8")
            (folder / "cases" / "CASE-9004-v2.md").write_text(source, encoding="utf-8")
            with mock.patch.object(knowledge, "KB_DIR", folder):
                self.assertEqual(self.search("装配机器人 伺服 电流偏高"), [])
                self.assertEqual(len(self.search("装配机器人 伺服 电流偏高", T("2026-09-30 00:00"))), 1)

    def test_case_without_updated_at_is_a_data_error(self):
        with tempfile_dir() as folder:
            (folder / "cases").mkdir()
            (folder / "cases" / "CASE-1.md").write_text(
                "---\nid: CASE-1\ncreated_at: 2026-09-01 09:00\nverified: true\n---\n正文", encoding="utf-8")
            with self.assertRaises(ValueError):
                knowledge.load_documents(folder)

    def test_case_verification_flag_is_returned(self):
        flags = {r["id"]: r.get("verified") for r in self.search("注塑机 温度升高")}
        self.assertEqual((flags.get("CASE-9001"), flags.get("CASE-9003")), (True, False))

    def test_unverified_case_cannot_support_a_suspected_part(self):
        event = "EV-IM011-0920T23"
        seen = index_by_id([{"id": event, "equipment_id": "EQ-IM-011"},
                            {"id": "EQ-IM-011", "key_parts": ["螺杆", "加热圈"]},
                            {"results": self.search("注塑机 螺杆 温度升高")}])
        report = {"event_id": event, "conclusion": "疑似螺杆问题", "status": "建议处置",
                  "evidence": [{"text": "知识库里有相似案例", "source": "CASE-9003"}],
                  "suspected_parts": [{"part": "螺杆", "status": "疑似", "reason": "相似案例",
                                       "evidence": ["CASE-9003"]}],
                  "ruled_out": [], "actions": [{"action": "安排点检", "risk": "低"}], "missing_evidence": []}
        problems = validate_report(report, event, seen)
        self.assertTrue(any("未验证的案例" in p for p in problems))
        report["suspected_parts"][0]["evidence"] = [event]  # 证据里可以提到相似案例，但不能拿它当根因依据
        self.assertEqual(validate_report(report, event, seen), [])


class tempfile_dir:
    def __enter__(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        return Path(self._dir.name)

    def __exit__(self, *exc):
        self._dir.cleanup()


if __name__ == "__main__":
    unittest.main()
