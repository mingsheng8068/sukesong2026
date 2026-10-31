import unittest

import pandas as pd

from pdm.adapters import OFFICIAL_DIR
from pdm.detection import SPIKE, SUSTAINED, events_as_of, official_detection
from pdm.priority import MAX_IMPACT, ONGOING, possible_orders, rank_events

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
AS_OF = pd.Timestamp("2026-09-21 14:00")
EQUIPMENT = pd.DataFrame({"equipment_id": ["EQ-A", "EQ-B"], "line": ["L01", "L02"]})
NO_CRITICALITY = pd.DataFrame(columns=["equipment_id", "criticality"])
ORDERS = pd.DataFrame({
    "id": [f"WO-{i}" for i in range(12)],
    "customer_level": ["战略客户"] * 8 + ["一般客户"] * 4,
    "lines": [["L01"]] * 12,
})


def event(event_id, kind=SUSTAINED, equipment_id="EQ-A", end="2026-09-21 13:00", peak_z=3.0, alarm_hours=6):
    return {"id": event_id, "kind": kind, "equipment_id": equipment_id, "signals": ("vib",),
            "start": pd.Timestamp("2026-09-21 05:00"), "end": pd.Timestamp(end), "alarm_hours": alarm_hours,
            "peak_z": peak_z, "peak_at": pd.Timestamp(end), "visible_from": pd.Timestamp("2026-09-21 05:00")}


def rank(events, **kwargs):
    kwargs.setdefault("criticality", NO_CRITICALITY)
    return rank_events(pd.DataFrame(events), AS_OF, equipment=EQUIPMENT, orders=ORDERS, **kwargs)


class RankTests(unittest.TestCase):
    def test_ongoing_first_then_by_score(self):
        table = rank([event("ended-big", end="2026-09-20 00:00", peak_z=9.0),
                      event("ongoing-small", peak_z=2.5), event("ongoing-big", peak_z=6.0)])
        self.assertEqual(table["id"].tolist(), ["ongoing-big", "ongoing-small", "ended-big"])
        self.assertEqual(table["state"].iloc[0], ONGOING)

    def test_spike_score_does_not_grow_with_its_z(self):
        small = rank([event("s", kind=SPIKE, peak_z=4.5, alarm_hours=1)])["score"].iloc[0]
        large = rank([event("s", kind=SPIKE, peak_z=19.0, alarm_hours=1)])["score"].iloc[0]
        self.assertEqual(small, large)

    def test_every_component_is_shown(self):
        table = rank([event("e")])
        for column in ("deviation_points", "duration_points", "impact_points", "orders_strategic",
                       "criticality", "factor", "score"):
            self.assertIn(column, table.columns)

    def test_missing_criticality_counts_as_one(self):
        row = rank([event("e")]).iloc[0]
        self.assertEqual((row["criticality"], row["factor"]), ("待补", 1.0))
        rated = rank([event("e")], criticality=pd.DataFrame({"equipment_id": ["EQ-A"], "criticality": ["A"]}))
        self.assertEqual(rated["score"].iloc[0], row["score"] * 1.5)

    def test_orders_listed_but_not_scored_by_default(self):
        row = rank([event("e")]).iloc[0]
        self.assertEqual(row["orders_strategic"], 8)
        self.assertEqual(row["impact_points"], 0)
        assumed = rank([event("e")], assume_orders_known=True).iloc[0]
        self.assertEqual(assumed["impact_points"], MAX_IMPACT)  # 8×3 + 4×1 = 28，封顶 20


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class OfficialRankTests(unittest.TestCase):
    def test_demo_time_puts_ongoing_sustained_events_on_top(self):
        table = rank_events(events_as_of(official_detection(), AS_OF), AS_OF)
        self.assertEqual(table["state"].iloc[0], ONGOING)
        self.assertIn("EV-IM011-0920T23", table[table["state"] == ONGOING]["id"].tolist())

    def test_candidate_orders_are_marked_unknown(self):
        candidates = possible_orders("L03", AS_OF)
        self.assertGreater(len(candidates), 0)
        self.assertTrue((candidates["known_at"] == "未知").all())



@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class OverviewTests(unittest.TestCase):
    def test_status_lights_at_demo_time(self):
        from pdm.overview import ALARMING, NORMAL, WATCHING, device_overview
        table = device_overview(official_detection(), AS_OF).set_index("equipment_id")
        for equipment_id in ("EQ-IM-011", "EQ-IM-012", "EQ-AGV-031"):
            self.assertEqual(table.loc[equipment_id, "status"], ALARMING)
        self.assertEqual(table.loc["EQ-CNC-001", "status"], WATCHING)  # 09-21 07:00 的温度尖峰在 24 小时内
        self.assertEqual(table.loc["EQ-OVN-041", "status"], NORMAL)
        self.assertEqual(len(table), 8)

    def test_overview_never_reads_after_as_of(self):
        from pdm.overview import device_overview
        early = device_overview(official_detection(), pd.Timestamp("2026-09-08 12:00")).set_index("equipment_id")
        self.assertTrue(pd.isna(early.loc["EQ-IM-011", "latest_event"]))  # 那时 IM-011 还没有事件

    def test_missing_data_is_not_normal(self):
        from pdm.overview import NO_DATA, device_overview
        early = device_overview(official_detection(), pd.Timestamp("2026-09-01 08:00"))
        self.assertEqual(set(early["status"]), {NO_DATA})  # 刚开始还没有基线，算不出偏离，不能亮“正常”
        self.assertTrue(early["vib_z"].isna().all() and early["last_z"].isna().all())

    def test_current_gap_shows_missing_and_keeps_alarm(self):
        from pdm.detection import Detection
        from pdm.overview import ALARMING, NO_DATA, device_overview
        detection = official_detection()
        hourly = detection.hourly.copy()
        gap = (hourly["equipment_id"].isin(["EQ-OVN-041", "EQ-IM-011"])
               & hourly["time"].between(AS_OF - pd.Timedelta(hours=1), AS_OF))
        hourly.loc[gap, "vib_z"] = float("nan")
        hourly.loc[gap, "vib_alarm"] = False
        table = device_overview(Detection(hourly, detection.sustained, detection.spike_parts), AS_OF)
        table = table.set_index("equipment_id")
        oven = table.loc["EQ-OVN-041"]
        self.assertEqual(oven["status"], NO_DATA)
        self.assertTrue(pd.isna(oven["vib_z"]))  # 不拿 12:00 的值冒充当前值
        self.assertEqual(oven["last_z_at"], pd.Timestamp("2026-09-21 12:00"))
        self.assertEqual(table.loc["EQ-IM-011", "status"], ALARMING)  # 已有告警不因数据缺失被清除


if __name__ == "__main__":
    unittest.main()
