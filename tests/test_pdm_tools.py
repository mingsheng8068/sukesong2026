import json
import unittest

import numpy as np
import pandas as pd

from pdm.adapters import OFFICIAL_DIR, load_sensors
from pdm.tools import get_event, get_failure_history, signal_summary, summarize

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
T = pd.Timestamp


class SummarizeTests(unittest.TestCase):
    """不依赖官方数据：信号摘要的统计规则。"""

    def test_constant_baseline_gives_no_z(self):
        for level in (1.5, 1.1, 45.1):
            with self.subTest(level):
                result = summarize(pd.Series([level + 0.5] * 24), pd.Series([level] * 168))
                self.assertFalse(result["enough_data"])
                self.assertNotIn("z", result)

    def test_normal_baseline_gives_finite_z(self):
        base = pd.Series(np.random.default_rng(0).normal(1.1, 0.05, 168))
        result = summarize(pd.Series([1.3] * 24), base)
        self.assertTrue(result["enough_data"])
        self.assertTrue(np.isfinite(result["z"]))


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class AsOfTests(unittest.TestCase):
    def test_failure_order_hidden_until_repair_is_done(self):
        # WO-2026-1000：09-11 14:00 发生，停机 457 分钟，21:37 修完才填好原因和处理方式
        before = get_failure_history("EQ-CNC-001", T("2026-09-11 21:00"))
        after = get_failure_history("EQ-CNC-001", T("2026-09-11 22:00"))
        self.assertNotIn("WO-2026-1000", [r["id"] for r in before["records"]])
        self.assertIn("WO-2026-1000", [r["id"] for r in after["records"]])

    def test_event_not_visible_before_it_starts(self):
        result = get_event("EV-RB022-0915T10", T("2026-09-15 09:00"))
        self.assertFalse(result["found"])

    def test_ongoing_event_only_counts_up_to_as_of(self):
        as_of = T("2026-09-15 16:00")
        early = get_event("EV-RB022-0915T10", as_of)
        late = get_event("EV-RB022-0915T10", T("2026-09-17 12:00"))
        self.assertEqual(early["hours_so_far"], 7)
        self.assertEqual(early["state"], "进行中")
        self.assertLessEqual(T(early["peak_at"]), as_of)
        self.assertLess(early["peak_z"], late["peak_z"])  # 之后更高的峰值看不到

    def test_signal_window_ends_at_as_of(self):
        as_of = T("2026-09-15 16:00")
        result = signal_summary("EQ-RB-022", "vib", as_of, hours=24)
        sensors = load_sensors()
        device = sensors[(sensors["equipment_id"] == "EQ-RB-022")
                         & sensors["time"].between(as_of - pd.Timedelta(hours=23), as_of)]
        self.assertEqual(result["window"][1], "2026-09-15 16:00")
        self.assertAlmostEqual(result["mean"], round(device["vib"].mean(), 3))
        self.assertAlmostEqual(result["max"], round(device["vib"].max(), 3))

    def test_noise_signals_are_not_offered(self):
        self.assertFalse(signal_summary("EQ-RB-022", "rpm", T("2026-09-15 16:00"))["found"])

    def test_results_are_json_serializable(self):
        as_of = T("2026-09-21 05:00")
        for result in (get_event("EV-IM011-0920T23", as_of), signal_summary("EQ-IM-011", "temp", as_of),
                       get_failure_history("EQ-IM-011", as_of), get_event("EV-AGV031-0921T03", as_of)):
            json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
