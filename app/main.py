"""PdM Agent 网页工作台入口：streamlit run app/main.py

侧边栏的“当前时刻”是全站共用的调查时点：队列、曲线、agent 能查到的数据都只到这一刻（DECISIONS D28）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # streamlit 以 app/ 为脚本目录运行，要能 import pdm、agent

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agent.llm import configured_model, load_dotenv  # noqa: E402
from agent.mock import SCENARIOS  # noqa: E402
from app import detail_page, orders_page, overview_page, queue_page  # noqa: E402
from app.common import OFFLINE  # noqa: E402
from pdm.adapters import load_sensors  # noqa: E402
from pdm.workorders import RUNTIME_DIR, Store  # noqa: E402

DEFAULT_AS_OF = pd.Timestamp("2026-09-21 14:00")
PAGES = {"设备总览": overview_page.render, "事件队列": queue_page.render, "事件详情": detail_page.render,
         "工单与审批记录": orders_page.render}


def sidebar() -> None:
    sensors = load_sensors()
    first, last = sensors["time"].min(), sensors["time"].max()
    st.sidebar.title("PdM Agent")
    st.sidebar.caption("设备告警处理工作台 · 官方脱敏仿真数据")
    st.sidebar.radio("页面", list(PAGES), key="page")

    st.sidebar.divider()
    st.sidebar.subheader("当前时刻（回放）")
    day = st.sidebar.date_input("日期", value=DEFAULT_AS_OF.date(), min_value=first.date(), max_value=last.date(),
                                key="day")
    hour = st.sidebar.slider("小时", 0, 23, value=DEFAULT_AS_OF.hour, key="hour")
    as_of = min(max(pd.Timestamp(day) + pd.Timedelta(hours=hour), first), last)
    st.session_state["as_of"] = as_of
    st.sidebar.caption(f"站在 {as_of:%Y-%m-%d %H:%M}：队列、曲线和 agent 都只能看到这之前的数据。")

    st.sidebar.divider()
    real = configured_model()
    options = [OFFLINE] + ([f"真实模型（{real}）"] if real else [])
    st.sidebar.radio("调查用的模型", options, key="model")
    if not real:
        st.sidebar.caption("没有配置 .env 里的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，只能用离线脚本模型。")
    if st.session_state["model"] == OFFLINE:
        st.sidebar.selectbox("离线演示场景", SCENARIOS, key="scenario",
                             help="脚本模型的步骤是写死的，只用来走通流程；tool_error 等场景演示失败出口")


def main() -> None:
    st.set_page_config(page_title="PdM Agent · 设备告警处理工作台", layout="wide")
    load_dotenv(ROOT / ".env")
    st.session_state.setdefault("page", "事件队列")
    sidebar()
    store = Store(os.environ.get("PDM_RUNTIME_DIR", RUNTIME_DIR))
    PAGES[st.session_state["page"]](store)


main()
