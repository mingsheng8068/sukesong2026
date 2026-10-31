"""生成题集标注模板 eval/pdm_golden.csv：每个事件一行，事件和默认调查时点已填好，结论一栏留给同事填。

    python eval/make_golden_template.py           # 文件已存在时不覆盖（保护已标注的内容）
    python eval/make_golden_template.py --force   # 确定要重新生成时才用

备注只写调查时点能看到的情况（类型、设备、信号、已告警几小时），不写之后有没有故障，免得影响专家判断。
格式见 docs/数据格式_给同事.md 第 6 节。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdm.adapters import SIGNALS, load_equipment  # noqa: E402
from pdm.detection import default_as_of, events_as_of, official_detection  # noqa: E402

GOLDEN = ROOT / "eval" / "pdm_golden.csv"
COLUMNS = ["event_id", "as_of", "expected_status", "expected_parts", "parts_basis", "must_cite", "notes"]


def template_rows() -> list[dict]:
    detection = official_detection()
    names = load_equipment().set_index("equipment_id")
    rows = []
    for event in detection.events.to_dict("records"):
        as_of = default_as_of(detection, event)
        seen = events_as_of(detection, as_of).set_index("id").loc[event["id"]]
        device = names.loc[event["equipment_id"]]
        rows.append({
            "event_id": event["id"], "as_of": f"{as_of:%Y-%m-%d %H:%M}", "expected_status": "", "expected_parts": "",
            "parts_basis": "专家判断", "must_cite": "",
            "notes": f"{event['kind']} · {device['name']}（{event['equipment_id']}，{device['line']}） · "
                     f"{'、'.join(SIGNALS[s][0] for s in event['signals'])} · 开始 {event['start']:%m-%d %H:%M} · "
                     f"调查时点已告警 {int(seen['alarm_hours'])} 小时",
        })
    return rows


def write_template(path: Path = GOLDEN, force: bool = False) -> int:
    if path.exists() and not force:
        raise FileExistsError(f"{path} 已存在，可能已经标注过；确定要覆盖请加 --force")
    rows = template_rows()
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # 带 BOM，Excel 直接打开不乱码
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成题集标注模板")
    parser.add_argument("--force", action="store_true", help="覆盖已有的 eval/pdm_golden.csv")
    args = parser.parse_args()
    try:
        count = write_template(force=args.force)
    except FileExistsError as exc:
        print(exc)
        return 1
    print(f"已生成 {GOLDEN.relative_to(ROOT)}：{count} 个事件，待标注 expected_status / expected_parts / must_cite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
