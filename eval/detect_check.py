"""检测评测：官方 16 次故障，有几次被提前预警、提前多少小时。和 agent 评测分开报。

    python eval/detect_check.py

口径：
- 一次故障算“被提前预警”：同一台设备有持续偏离事件，开始时间在故障前 72 小时内；提前量 = 故障时间 − 最早那个事件的开始；
- 事件的替代标签（官方数据没有误报标签）：开始后 72 小时内这台设备有故障 →“72h 内有故障”；没有故障且
  72 小时都在观察期内 →“72h 内无故障”；没有故障但观察期不足 72 小时 →“随访不足”，单列，不算负例；
- 观察期截止：官方故障单没有写覆盖到哪天，按传感器数据的最后一小时算；
- 检测参数是在这 16 次故障上调定的（DECISIONS D23），覆盖率随基线取法在 15/16 到 16/16 之间，不是独立验证。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdm.adapters import load_failures, load_sensors  # noqa: E402
from pdm.detection import SPIKE, SUSTAINED, official_detection  # noqa: E402

HORIZON = pd.Timedelta(hours=72)


def coverage(events: pd.DataFrame, failures: pd.DataFrame) -> list[dict]:
    rows = []
    for failure in failures.to_dict("records"):
        before = events[(events["equipment_id"] == failure["equipment_id"])
                        & (events["start"] < failure["occurred_at"])
                        & (events["start"] >= failure["occurred_at"] - HORIZON)]
        first = before.sort_values("start").iloc[0] if len(before) else None
        rows.append({
            "failure": failure["id"], "equipment_id": failure["equipment_id"], "occurred_at": failure["occurred_at"],
            "event": None if first is None else first["id"],
            "lead_h": None if first is None else (failure["occurred_at"] - first["start"]) / pd.Timedelta(hours=1),
        })
    return rows


def follow_up(events: pd.DataFrame, failures: pd.DataFrame, observed_until: pd.Timestamp) -> list[str]:
    """每个事件的替代标签：72h 内有故障 / 72h 内无故障 / 随访不足。"""
    labels = []
    for event in events.to_dict("records"):
        same = failures[failures["equipment_id"] == event["equipment_id"]]
        window_end = event["start"] + HORIZON
        if ((same["occurred_at"] > event["start"]) & (same["occurred_at"] <= window_end)).any():
            labels.append("72h 内有故障")
        elif window_end <= observed_until:
            labels.append("72h 内无故障")
        else:
            labels.append("随访不足")
    return labels


def _label_counts(labels: list[str]) -> str:
    return "，".join(f"{name} {labels.count(name)} 个" for name in ("72h 内有故障", "72h 内无故障", "随访不足"))


def main() -> int:
    events = official_detection().events
    failures = load_failures()
    observed_until = load_sensors()["time"].max()
    sustained = events[events["kind"] == SUSTAINED]
    spikes = events[events["kind"] == SPIKE]

    rows = coverage(sustained, failures)
    for row in rows:
        lead = "未提前预警" if row["event"] is None else f"提前 {row['lead_h']:.0f}h（{row['event']}）"
        print(f"{row['failure']} {row['equipment_id']} {row['occurred_at']:%m-%d %H:%M}  {lead}")
    leads = [row["lead_h"] for row in rows if row["event"] is not None]
    print(f"\n覆盖 {len(leads)}/{len(rows)}；提前量中位数 {pd.Series(leads).median():.0f}h"
          f"（{min(leads):.0f}–{max(leads):.0f}h）")
    print(f"观察期截止 {observed_until:%Y-%m-%d %H:%M}（替代标签，官方数据没有误报标签）")
    print(f"持续偏离 {len(sustained)} 个：{_label_counts(follow_up(sustained, failures, observed_until))}")
    print(f"疑似传感器尖峰 {len(spikes)} 个：{_label_counts(follow_up(spikes, failures, observed_until))}")
    print("\n口径：检测参数在这 16 次故障上调定（D23），覆盖率随基线取法在 15/16 到 16/16 之间；换到真实设备需要重新标定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
