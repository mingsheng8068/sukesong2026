"""信号曲线：只画到当前时刻，事件区间高亮，当前时刻画竖线。"""

from __future__ import annotations

import altair as alt
import pandas as pd

from pdm.adapters import SIGNALS, load_sensors
from pdm.detection import official_detection

HOURS_BEFORE = 72


def signal_chart(equipment_id: str, signal: str, as_of: pd.Timestamp, event: dict | None = None) -> alt.Chart:
    start = as_of - pd.Timedelta(hours=HOURS_BEFORE)
    if event is not None:
        start = min(start, event["start"] - pd.Timedelta(hours=24))
    sensors = load_sensors()
    device = sensors[(sensors["equipment_id"] == equipment_id) & sensors["time"].between(start, as_of)]
    name, unit = SIGNALS[signal]
    data = pd.DataFrame({"时间": device["time"], "数值": device[signal], "曲线": "原始值"})
    if signal == "vib":
        hourly = official_detection().hourly
        mean6 = hourly[(hourly["equipment_id"] == equipment_id) & hourly["time"].between(start, as_of)]
        data = pd.concat([data, pd.DataFrame({"时间": mean6["time"], "数值": mean6["vib_mean6"], "曲线": "6 小时均值"})])

    x = alt.X("时间:T", title=None, axis=alt.Axis(format="%m-%d %H时"), scale=alt.Scale(domain=[start, as_of]))
    lines = alt.Chart(data).mark_line(strokeWidth=1.5).encode(
        x=x, y=alt.Y("数值:Q", title=unit, scale=alt.Scale(zero=False)),
        color=alt.Color("曲线:N", legend=alt.Legend(orient="top", title=None)),
        strokeDash=alt.StrokeDash("曲线:N", legend=None),
    )
    layers = []
    if event is not None:
        band_end = min(event["end"] + pd.Timedelta(hours=1), as_of)
        band = pd.DataFrame({"开始": [event["start"]], "结束": [max(band_end, event["start"])]})
        layers.append(alt.Chart(band).mark_rect(color="#f59e0b", opacity=0.15).encode(x="开始:T", x2="结束:T"))
    now = alt.Chart(pd.DataFrame({"当前时刻": [as_of]})).mark_rule(color="#dc2626", strokeDash=[4, 4]).encode(
        x="当前时刻:T")
    return alt.layer(*layers, lines, now).properties(title=f"{name}（{unit}）", height=170)
