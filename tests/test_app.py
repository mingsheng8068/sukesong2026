"""页面无头测试（streamlit.testing.v1.AppTest）：存储指向临时目录，不碰 data/runtime/。"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from pdm.adapters import OFFICIAL_DIR
from pdm.workorders import Store

HAS_OFFICIAL = (OFFICIAL_DIR / "02_设备传感器时序.xlsx").exists()
EVENT = "EV-IM011-0920T23"
LLM_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
APP = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@unittest.skipUnless(HAS_OFFICIAL, "缺少 data/official/，见 README")
class AppTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest

        self.dir = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ("PDM_RUNTIME_DIR", *LLM_KEYS)}
        os.environ["PDM_RUNTIME_DIR"] = self.dir.name
        for key in LLM_KEYS:
            os.environ[key] = ""  # 不读真实模型配置（load_dotenv 不覆盖已有变量）
        self.at = AppTest.from_file(APP, default_timeout=60).run()

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.dir.cleanup()

    def labels(self):
        return [b.label for b in self.at.button]

    def open_and_investigate(self, scenario="ok"):
        self.at.sidebar.radio(key="page").set_value("事件队列").run()
        self.at.selectbox(key="scenario").set_value(scenario)
        self.at.selectbox(key="queue_pick").set_value(EVENT).run()
        self.at.button(key="open_event").click().run()
        self.at.button(key="investigate").click().run()
        self.assertEqual([e.value for e in self.at.exception], [])

    def test_three_pages_render(self):
        for page in ("设备总览", "事件队列", "事件详情", "工单与审批记录"):
            self.at.sidebar.radio(key="page").set_value(page).run()
            self.assertEqual([e.value for e in self.at.exception], [], page)

    def test_overview_opens_latest_event(self):
        self.at.sidebar.radio(key="page").set_value("设备总览").run()
        self.assertTrue(any("告警中" in m.value for m in self.at.markdown))
        self.at.button(key="overview_EQ-IM-011").click().run()
        self.assertEqual((self.at.session_state["page"], self.at.session_state["event_id"]),
                         ("事件详情", "EV-IM011-0920T23"))
        self.assertEqual([e.value for e in self.at.exception], [])

    def test_overview_without_baseline_shows_no_data(self):
        import datetime
        self.at.sidebar.date_input[0].set_value(datetime.date(2026, 9, 1))
        self.at.sidebar.slider(key="hour").set_value(8)
        self.at.sidebar.radio(key="page").set_value("设备总览").run()
        self.assertEqual([e.value for e in self.at.exception], [])
        self.assertTrue(any("数据不足 **8** 台" in m.value for m in self.at.markdown))
        self.assertTrue(any("当前振动 z 缺失（还没有有效值）" in c.value for c in self.at.caption))

    def test_only_offline_model_without_config(self):
        self.assertEqual(self.at.sidebar.radio(key="model").options, ["离线脚本模型"])

    def test_approve_creates_exactly_one_work_order(self):
        self.open_and_investigate()
        self.assertIn("通过并生成工单", self.labels())
        next(b for b in self.at.button if b.label == "通过并生成工单").click().run()
        decisions = Store(self.dir.name).list_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["work_order"]["id"], "MWO-0921-01")
        self.assertNotIn("通过并生成工单", self.labels())  # 已有决定：审批区不再出现
        self.at.run()
        self.assertEqual(len(Store(self.dir.name).list_decisions()), 1)

    def test_changing_time_makes_report_stale(self):
        self.open_and_investigate()
        self.at.sidebar.slider(key="hour").set_value(13).run()
        self.assertTrue(any("已过期" in w.value for w in self.at.warning))
        self.assertNotIn("通过并生成工单", self.labels())

    def test_spike_can_only_be_closed_as_false_alarm_with_reason(self):
        self.at.sidebar.slider(key="hour").set_value(4).run()
        self.at.selectbox(key="queue_pick").set_value("EV-AGV031-0921T03").run()
        self.at.button(key="open_event").click().run()
        self.at.button(key="investigate").click().run()
        self.assertNotIn("通过并生成工单", self.labels())
        close = next(b for b in self.at.button if b.label == "确认误报并关闭")
        close.click().run()
        self.assertTrue(any("要写理由" in e.value for e in self.at.error))  # 空理由被拒
        self.at.text_input(key="false_alarm_reason").set_value("现场确认温度探头接线松动")
        next(b for b in self.at.button if b.label == "确认误报并关闭").click().run()
        decisions = Store(self.dir.name).list_decisions()
        self.assertEqual([d["decision"] for d in decisions], ["确认误报并关闭"])
        self.assertNotIn("work_order", decisions[0])

    def test_tool_error_is_incomplete_without_approval(self):
        self.open_and_investigate("tool_error")
        self.assertTrue(any("调查未完成" in e.value for e in self.at.error))
        self.assertNotIn("通过并生成工单", self.labels())

    def test_failed_reinvestigation_retires_old_report(self):
        # Codex 复核 P1：先正常调查，再重新调查但记录写不进去 → 旧报告不能再审批出单
        self.open_and_investigate()
        self.assertIn("通过并生成工单", self.labels())
        from unittest import mock
        try:
            patch = mock.patch.object(Store, "save_investigation", side_effect=OSError("只读目录"))
            patch.start()
            self.at.selectbox(key="scenario").set_value("tool_error")
            self.at.button(key="investigate").click().run()
            self.assertEqual([e.value for e in self.at.exception], [])
            self.assertTrue(any("保存失败" in e.value for e in self.at.error))
            self.assertNotIn("通过并生成工单", self.labels())
            self.at.run()  # 刷新后也不能恢复审批资格
            self.assertNotIn("通过并生成工单", self.labels())
        finally:
            patch.stop()
        self.assertEqual(Store(self.dir.name).list_decisions(), [])

    def test_malformed_report_is_incomplete_and_page_does_not_crash(self):
        # Codex 复核 P2：契约拒绝的报告（缺字段或字段类型错误）不进入正常渲染
        for scenario in ("missing_fields", "wrong_types"):
            with self.subTest(scenario):
                self.open_and_investigate(scenario)
                self.assertTrue(any("调查未完成" in e.value for e in self.at.error))
                self.assertNotIn("通过并生成工单", self.labels())
                self.assertTrue(any("已取得的工具结果" in m.value for m in self.at.markdown))


if __name__ == "__main__":
    unittest.main()
