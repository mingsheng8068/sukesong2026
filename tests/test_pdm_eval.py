import csv
import tempfile
import unittest
from pathlib import Path

from agent.mock import scripted_llm
from eval.make_golden_template import COLUMNS, write_template
from eval.run_pdm_eval import _show, evaluate, load_golden
from pdm.adapters import OFFICIAL_DIR

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()


def write_golden(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({**{c: "" for c in COLUMNS}, **row} for row in rows)


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class GoldenTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "golden.csv"

    def tearDown(self):
        self.dir.cleanup()

    def test_template_has_every_event_and_never_overwrites(self):
        self.assertEqual(write_template(self.path), 35)
        rows, unlabeled = load_golden(self.path)
        self.assertEqual((rows, unlabeled), ([], 35))
        with self.assertRaises(FileExistsError):
            write_template(self.path)

    def test_template_notes_do_not_leak_outcomes(self):
        write_template(self.path)
        text = self.path.read_text(encoding="utf-8-sig")
        self.assertNotIn("故障", text)
        self.assertNotIn("WO-2026", text)

    def test_mock_run_scores_labeled_rows(self):
        write_golden(self.path, [
            {"event_id": "EV-IM011-0920T23", "as_of": "2026-09-21 14:00", "expected_status": "建议处置",
             "expected_parts": "加热圈", "must_cite": "WO-2026-1004"},
            {"event_id": "EV-AGV031-0921T03", "as_of": "2026-09-21 04:00", "expected_status": "疑似误报"},
            {"event_id": "EV-IM012-0920T09", "as_of": "2026-09-20 14:00"},  # 未标注：跳过
        ])
        rows, unlabeled = load_golden(self.path)
        self.assertEqual((len(rows), unlabeled), (2, 1))
        summary = evaluate(rows, lambda event_id: scripted_llm(event_id, "ok"), trials=2)
        self.assertEqual((summary["questions"], summary["runs"]), (2, 4))
        self.assertEqual((summary["contract_pass"], summary["status_accuracy"]), ({"hit": 4, "total": 4},) * 2)
        self.assertEqual(summary["pass_k"], {"hit": 2, "total": 2})
        self.assertEqual(summary["must_cite_hit"], {"hit": 2, "total": 2})  # 只有第一题标了 must_cite，跑 2 次

    def test_wrong_label_or_missing_citation_fails(self):
        write_golden(self.path, [
            {"event_id": "EV-IM011-0920T23", "as_of": "2026-09-21 14:00", "expected_status": "需持续观察；需补充证据"},
            {"event_id": "EV-IM011-0920T23", "as_of": "2026-09-21 14:00", "expected_status": "建议处置",
             "must_cite": "KB-NOT-THERE"},
        ])
        summary = evaluate(load_golden(self.path)[0], lambda event_id: scripted_llm(event_id, "ok"), trials=1)
        self.assertEqual([item["pass_k"] for item in summary["items"]], [False, False])
        self.assertEqual(summary["must_cite_hit"], {"hit": 0, "total": 1})  # 第一题没标 must_cite，不算命中

    def test_unlabeled_metrics_are_not_scored(self):
        write_golden(self.path, [{"event_id": "EV-AGV031-0921T03", "as_of": "2026-09-21 04:00",
                                  "expected_status": "疑似误报"}])
        rows = load_golden(self.path)[0]
        summary = evaluate(rows, lambda event_id: scripted_llm(event_id, "ok"), trials=1)
        self.assertEqual((summary["parts_hit_expert"], summary["must_cite_hit"]),
                         ({"hit": 0, "total": 0}, {"hit": 0, "total": 0}))
        self.assertEqual(_show(summary["parts_hit_expert"]), "未评测（没有标注）")
        for trials in (0, -1):
            with self.assertRaises(ValueError):
                evaluate(rows, lambda event_id: scripted_llm(event_id, "ok"), trials=trials)


if __name__ == "__main__":
    unittest.main()
