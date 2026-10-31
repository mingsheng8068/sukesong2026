import unittest

import numpy as np
import pandas as pd

from pdm.adapters import OFFICIAL_DIR, load_failures, load_sensors
from pdm.detection import (
    EVENT_COLUMNS, MAX_SPIKE_H, SPIKE, SUSTAINED, detect, events_as_of, official_detection, spike_z, vibration_alarms,
)

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
COMPARE = ["id", "kind", "start", "end", "alarm_hours", "peak_z"]


class VibrationRuleTests(unittest.TestCase):
    """不依赖官方数据：用构造的序列检查规则本身。"""

    def test_step_up_alarms_and_missing_values_do_not_break(self):
        rng = np.random.default_rng(0)
        values = rng.normal(1.5, 0.05, 300)
        values[250:] += 0.5            # 第 250 小时起振动抬高
        values[[100, 260]] = np.nan    # 单独缺 1 小时
        _, z, alarm = vibration_alarms(pd.Series(values))
        self.assertFalse(alarm[:250].any())
        self.assertTrue(alarm[255:].all())
        self.assertTrue(np.isfinite(z[255:]).all())

    def test_frozen_baseline_keeps_alarming_during_long_rise(self):
        values = np.full(400, 1.5) + np.random.default_rng(1).normal(0, 0.05, 400)
        values[200:] += 0.5            # 抬高后持续很久：基线剔除告警小时，不会被“学”成正常
        _, _, alarm = vibration_alarms(pd.Series(values))
        self.assertTrue(alarm[210:].all())

    def test_zero_variance_baseline_is_undecidable(self):
        # 1.1、45.1 这类常量算出的标准差是 1e-16 级的浮点误差，不是 0，也要判为没有波动
        for level in (1.5, 1.1, 45.1):
            with self.subTest(level):
                _, z, alarm = vibration_alarms(pd.Series([level] * 150 + [level + 0.5] * 20))
                self.assertTrue(np.isnan(z[150:156]).all())  # 基线还全是常量的那几个小时：不可判定，不告警
                self.assertFalse(alarm[150:156].any())
                self.assertFalse(np.isinf(z).any())
                self.assertTrue(np.isnan(spike_z(pd.Series([level] * 100 + [level + 10]))).iloc[-1])

    def test_no_events_gives_empty_table(self):
        flat = synthetic_sensors(200).assign(vib=1.5, temp=45.0, current=10.0)
        detection = detect(flat)
        self.assertTrue(detection.events.empty)
        self.assertEqual(list(detection.events.columns), EVENT_COLUMNS)
        self.assertTrue(events_as_of(detection, flat["time"].max()).empty)


def synthetic_sensors(hours: int) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "time": pd.date_range("2026-09-01", periods=hours, freq="h"), "equipment_id": "EQ-X-001",
        "vib": rng.normal(1.5, 0.05, hours), "temp": rng.normal(45, 0.5, hours), "current": rng.normal(10, 0.2, hours),
        "rpm": 0, "load": 0, "humidity": 0,
    })


class SpikeAsOfTests(unittest.TestCase):
    """审查发现的问题：尖峰的结果不能被 as_of 之后的数据改变。"""

    H = 200

    def test_spike_at_as_of_does_not_depend_on_later_data(self):
        sensors = synthetic_sensors(300)
        sensors.loc[self.H, "temp"] += 5               # 温度只跳 1 小时
        sensors.loc[self.H:self.H + 2, "current"] += 25  # 电流连跳 3 小时，不是孤立尖峰
        full = detect(sensors)
        for k in range(1, 5):
            as_of = sensors["time"][self.H + k]
            live = detect(sensors[sensors["time"] <= as_of])
            expected = events_as_of(full, as_of)[COMPARE + ["signals"]].reset_index(drop=True)
            actual = events_as_of(live, as_of)[COMPARE + ["signals"]].reset_index(drop=True)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
        spike = full.events[full.events["start"] == sensors["time"][self.H]].iloc[0]
        self.assertEqual(spike["signals"], ("temp",))

    def test_missing_next_hour_is_not_recovery(self):
        sensors = synthetic_sensors(300)
        sensors.loc[self.H, "temp"] += 5
        sensors.loc[self.H + 1, "temp"] = np.nan
        detection = detect(sensors)
        start = sensors["time"][self.H]
        at = lambda k: events_as_of(detection, sensors["time"][self.H + k])
        self.assertFalse((at(1)["start"] == start).any())   # 下一小时缺失：还确认不了
        self.assertTrue((at(2)["start"] == start).any())    # 再下一小时正常：确认是孤立尖峰

    def test_missing_then_still_high_is_not_isolated(self):
        sensors = synthetic_sensors(300)
        sensors.loc[[self.H, self.H + 2], "temp"] += 5
        sensors.loc[self.H + 1, "temp"] = np.nan
        detection = detect(sensors)
        self.assertFalse(detection.spike_parts["start"].isin(sensors["time"][self.H:self.H + 3]).any())


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class OfficialDetectionTests(unittest.TestCase):
    def setUp(self):
        self.detection = official_detection()
        self.events = self.detection.events

    def test_reads_all_hours_with_missing_vibration(self):
        sensors = load_sensors()
        self.assertEqual(len(sensors), 5760)
        self.assertEqual(int(sensors["vib"].isna().sum()), 71)

    def test_locked_counts_and_coverage(self):
        # 参数在 16 次故障上调定（D23）；改参数时这里会提醒，要有意识地更新
        from eval.detect_check import coverage

        sustained = self.events[self.events["kind"] == SUSTAINED]
        self.assertEqual(len(sustained), 14)
        self.assertEqual(int((self.events["kind"] == SPIKE).sum()), 21)
        rows = coverage(sustained, load_failures())
        self.assertEqual(sum(row["event"] is not None for row in rows), 16)

    def test_noise_signals_never_create_events(self):
        used = {signal for signals in self.events["signals"] for signal in signals}
        self.assertTrue(used <= {"vib", "temp", "current"})

    def test_spikes_are_short_single_signal_and_vibration_quiet(self):
        hourly = self.detection.hourly
        for spike in self.events[self.events["kind"] == SPIKE].to_dict("records"):
            self.assertEqual(len(spike["signals"]), 1)
            self.assertLessEqual(spike["alarm_hours"], MAX_SPIKE_H)
            during = hourly[(hourly["equipment_id"] == spike["equipment_id"])
                            & hourly["time"].between(spike["start"], spike["end"])]
            self.assertFalse(during["vib_alarm"].any())

    def test_first_day_only_has_no_events_and_does_not_crash(self):
        sensors = load_sensors()
        self.assertTrue(detect(sensors[sensors["time"] < "2026-09-02 08:00"]).events.empty)

    def test_follow_up_labels_keep_short_observation_apart(self):
        from eval.detect_check import follow_up

        spikes = self.events[self.events["kind"] == SPIKE]
        labels = follow_up(spikes, load_failures(), load_sensors()["time"].max())
        self.assertEqual([labels.count(x) for x in ("72h 内有故障", "72h 内无故障", "随访不足")], [2, 18, 1])
        self.assertEqual(spikes[[x == "随访不足" for x in labels]]["id"].tolist(), ["EV-RB022-0930T08"])

    def test_truncating_data_at_as_of_gives_the_same_events(self):
        sensors = load_sensors()
        for as_of in ["2026-09-10 20:00", "2026-09-16 12:00", "2026-09-21 06:00"]:
            as_of = pd.Timestamp(as_of)
            live = detect(sensors[sensors["time"] <= as_of])
            expected = events_as_of(self.detection, as_of)[COMPARE + ["signals"]].reset_index(drop=True)
            actual = events_as_of(live, as_of)[COMPARE + ["signals"]].reset_index(drop=True)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False)


if __name__ == "__main__":
    unittest.main()
