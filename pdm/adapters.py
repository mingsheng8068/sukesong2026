"""数据接入：把官方素材包读成统一字段的表。

换一份数据（比如客户的 MES / SCADA 导出），只改下面的文件名和 COLUMN_MAP，后面的检测、工具不用动。
返回的表有缓存，调用方只读不改。
"""

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path

import pandas as pd

OFFICIAL_DIR = Path(__file__).resolve().parent.parent / "data" / "official" / "data"
SYNTHETIC_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"

FILES = {
    "equipment": "01_设备主数据.xlsx",
    "sensors": "02_设备传感器时序.xlsx",
    "failures": "03_设备故障维修记录.csv",
    "orders": "05_生产工单.csv",
}

# 官方列名 → 内部字段名
COLUMN_MAP = {
    "equipment": {
        "设备编号": "equipment_id", "设备名称": "name", "所属产线": "line", "投产日期": "commissioned",
        "设计节拍(秒)": "cycle_s", "维保周期(天)": "maint_cycle_days", "关键部件": "key_parts",
    },
    "sensors": {
        "时间戳": "time", "设备编号": "equipment_id", "主轴振动RMS(mm/s)": "vib", "主轴温度(℃)": "temp",
        "主轴电流(A)": "current", "转速(rpm)": "rpm", "负载率(%)": "load", "环境湿度(%RH)": "humidity",
    },
    "failures": {
        "工单号": "id", "设备编号": "equipment_id", "故障发生时间": "occurred_at", "故障现象": "symptom",
        "故障原因": "cause", "停机时长(分钟)": "downtime_min", "维修费用(元)": "cost",
        "损失产量(件)": "lost_qty", "处理方式": "action",
    },
    "orders": {
        "工单号": "id", "产品型号": "product", "订单量(件)": "qty", "客户等级": "customer_level",
        "交期": "due", "单件工时(秒)": "unit_s", "可产线": "lines", "优先级得分": "official_priority",
    },
}

# 合成补充表（同事按 docs/数据格式_给同事.md 填），表头必须一致；每次调用都重新读，文件放进来不用重启
SYNTHETIC = {
    "criticality": ("equipment_criticality.csv",
                    ["equipment_id", "criticality", "is_bottleneck", "reason", "source"], []),
    "maintenance": ("maintenance.csv",
                    ["record_id", "equipment_id", "component", "maint_type", "start_time", "end_time", "content",
                     "result", "next_planned_date", "source"], ["start_time", "end_time", "next_planned_date"]),
    "spare_parts": ("spare_parts.csv",
                    ["part_id", "part_name", "equipment_type", "component", "stock_qty", "safety_stock",
                     "lead_time_days", "unit_price", "location", "source", "snapshot_at"], ["snapshot_at"]),
}

# 信号的中文名和单位（给工具结果和页面用）
SIGNALS = {
    "vib": ("主轴振动RMS", "mm/s"),
    "temp": ("主轴温度", "℃"),
    "current": ("主轴电流", "A"),
    "rpm": ("转速", "rpm"),
    "load": ("负载率", "%"),
    "humidity": ("环境湿度", "%RH"),
}


def _read(kind: str) -> pd.DataFrame:
    path = OFFICIAL_DIR / FILES[kind]
    if not path.exists():
        raise FileNotFoundError(f"缺少官方数据 {path}；放置方法见 README 的“数据”一节")
    if path.suffix == ".csv":
        table = pd.read_csv(path, encoding="utf-8-sig")
    else:
        with warnings.catch_warnings():  # 官方 xlsx 没有默认样式，openpyxl 会提示，不影响读数
            warnings.simplefilter("ignore")
            table = pd.read_excel(path)
    return table.rename(columns=COLUMN_MAP[kind])[list(COLUMN_MAP[kind].values())]


def short_code(equipment_id: str) -> str:
    """EQ-RB-022 → RB022，用在事件和信号摘要的编号里。"""
    return equipment_id.removeprefix("EQ-").replace("-", "")


@lru_cache(maxsize=1)
def load_equipment() -> pd.DataFrame:
    table = _read("equipment")
    table["key_parts"] = table["key_parts"].str.split("/")
    return table


@lru_cache(maxsize=1)
def load_sensors() -> pd.DataFrame:
    """逐小时传感器数据，按设备、时间排序。缺失值保留为 NaN，不在这里插值。"""
    table = _read("sensors")
    table["time"] = pd.to_datetime(table["time"])
    return table.sort_values(["equipment_id", "time"]).reset_index(drop=True)


@lru_cache(maxsize=1)
def load_failures() -> pd.DataFrame:
    """故障维修单。原因和处理方式修完才填，官方没有填写时间，按“发生时间 + 停机时长”作为最早可见时间。"""
    table = _read("failures")
    table["occurred_at"] = pd.to_datetime(table["occurred_at"])
    table["visible_from"] = table["occurred_at"] + pd.to_timedelta(table["downtime_min"], unit="min")
    return table.sort_values(["equipment_id", "occurred_at"]).reset_index(drop=True)


@lru_cache(maxsize=1)
def load_orders() -> pd.DataFrame:
    """生产工单。官方只有交期、没有创建时间，所以某个时刻是否已经知道这张订单无法判断（见 pdm/priority.py）。"""
    table = _read("orders")
    table["due"] = pd.to_datetime(table["due"])
    table["lines"] = table["lines"].str.split(",")
    return table


def read_synthetic(kind: str) -> pd.DataFrame | None:
    """读一张合成补充表；文件还没放进来返回 None（“未接入”），表头不对直接报错（数据问题，不让 agent 猜）。"""
    name, columns, dates = SYNTHETIC[kind]
    path = SYNTHETIC_DIR / name
    if not path.exists():
        return None
    table = pd.read_csv(path, encoding="utf-8-sig")
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise ValueError(f"{name} 缺少列 {missing}，格式见 docs/数据格式_给同事.md")
    for column in dates:
        table[column] = pd.to_datetime(table[column])
    return table[columns]


def load_criticality() -> pd.DataFrame:
    """设备重要性。文件还没到时返回空表，页面显示“待补”。"""
    table = read_synthetic("criticality")
    return pd.DataFrame(columns=SYNTHETIC["criticality"][1]) if table is None else table


def load_maintenance() -> pd.DataFrame | None:
    return read_synthetic("maintenance")


def load_spare_parts() -> pd.DataFrame | None:
    return read_synthetic("spare_parts")
