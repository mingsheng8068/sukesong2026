"""agent 题集评测：读 eval/pdm_golden.csv 里已标注的行，每题跑 k 次，和检测评测（detect_check.py）分开报。

    python eval/run_pdm_eval.py --mock              # 离线脚本模型：只检查评测流程本身，不代表诊断质量
    python eval/run_pdm_eval.py --trials 3          # 真实模型（读 .env），每题 3 次，看结论稳不稳
    python eval/run_pdm_eval.py --only EV-IM011-0920T23

一次运行算“通过”：契约通过 + 状态在标注范围内 + must_cite 全部被引用（标了才查）+ 疑似部位命中（标了才查）。
pass^k：k 次全部通过才算这道题稳定通过。expected_status、expected_parts、must_cite 多个值用“；”分隔。
每项指标只统计有对应标注的题，报“命中 / 分母”；没有一题标注的指标写“未评测”，不算成 100%。
expected_parts 是专家判断：官方数据的故障原因是随机生成的，部位对不对没法用数据验证，单独列出、不混进状态准确率。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.llm import OpenAICompatLLM, load_dotenv  # noqa: E402
from agent.loop import run_investigation  # noqa: E402
from agent.mock import scripted_llm  # noqa: E402

GOLDEN = ROOT / "eval" / "pdm_golden.csv"
LAST_RUN = ROOT / "eval" / "last_pdm_run.json"


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").replace(";", "；").split("；") if v.strip()]


def load_golden(path: Path = GOLDEN) -> tuple[list[dict], int]:
    """返回（已标注的题，未标注的行数）。"""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    labeled = [r for r in rows if _split(r.get("expected_status"))]
    return labeled, len(rows) - len(labeled)


def judge(row: dict, result) -> dict:
    report = result.report if not result.problems else None
    cited = set()
    parts = []
    if report:
        cited = {e["source"] for e in report["evidence"]}
        cited |= {s for p in report["suspected_parts"] + report["ruled_out"] for s in p["evidence"]}
        parts = [p["part"] for p in report["suspected_parts"]]
    expected_parts, must_cite = _split(row.get("expected_parts")), _split(row.get("must_cite"))
    checks = {  # None：这题没标这一项，不参与统计
        "contract": not result.problems,
        "status": result.status in _split(row["expected_status"]),
        "must_cite": set(must_cite) <= cited if must_cite else None,
        "parts": any(e in p or p in e for e in expected_parts for p in parts) if expected_parts else None,
    }
    return {"status": result.status, "problems": result.problems, "parts": parts, "checks": checks,
            "passed": all(v for v in checks.values() if v is not None)}


def _tally(values: list[bool | None]) -> dict:
    """只数有标注的：{"hit": 命中数, "total": 分母}；分母为 0 就是未评测。"""
    labeled = [v for v in values if v is not None]
    return {"hit": sum(labeled), "total": len(labeled)}


def _show(tally: dict) -> str:
    if not tally["total"]:
        return "未评测（没有标注）"
    return f"{tally['hit']}/{tally['total']}（{tally['hit'] / tally['total']:.0%}）"


def evaluate(rows: list[dict], make_llm, trials: int) -> dict:
    if not isinstance(trials, int) or trials < 1:
        raise ValueError("trials 至少为 1")
    items = []
    for row in rows:
        as_of = pd.Timestamp(row["as_of"])
        runs = [judge(row, run_investigation(row["event_id"], as_of, make_llm(row["event_id"])))
                for _ in range(trials)]
        items.append({"event_id": row["event_id"], "as_of": row["as_of"], "expected": row["expected_status"],
                      "runs": runs, "pass_k": all(r["passed"] for r in runs)})
    total = [r for item in items for r in item["runs"]]
    count = lambda key: _tally([r["checks"][key] for r in total])  # noqa: E731
    return {
        "trials": trials, "questions": len(items), "runs": len(total),
        "contract_pass": count("contract"), "status_accuracy": count("status"), "must_cite_hit": count("must_cite"),
        "parts_hit_expert": count("parts"), "pass_k": _tally([item["pass_k"] for item in items]),
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PdM agent 题集评测")
    parser.add_argument("--mock", action="store_true", help="用离线脚本模型（只检查流程）")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--only", nargs="*", help="只跑这些事件")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials 至少为 1")

    if not args.golden.exists():
        print(f"没有 {args.golden}；先运行 python eval/make_golden_template.py，请同事标注后再评测")
        return 1
    rows, unlabeled = load_golden(args.golden)
    if args.only:
        rows = [r for r in rows if r["event_id"] in args.only]
    if not rows:
        print(f"没有已标注的题（未标注 {unlabeled} 行）")
        return 1
    load_dotenv(ROOT / ".env")
    make_llm = (lambda event_id: scripted_llm(event_id, "ok")) if args.mock else (lambda _: OpenAICompatLLM.from_env())
    summary = evaluate(rows, make_llm, args.trials)

    print(f"{'离线脚本模型（只检查流程）' if args.mock else '真实模型'}：{summary['questions']} 题 × {args.trials} 次"
          f"（未标注 {unlabeled} 行跳过）")
    for item in summary["items"]:
        statuses = "、".join(r["status"] for r in item["runs"])
        print(f"{'✓' if item['pass_k'] else '✗'} {item['event_id']} @ {item['as_of']}  期望 {item['expected']}  实际 {statuses}")
    print(f"\n按次数：契约通过 {_show(summary['contract_pass'])} · 状态准确 {_show(summary['status_accuracy'])} · "
          f"must_cite 命中 {_show(summary['must_cite_hit'])}")
    print(f"按题：pass^{args.trials} {_show(summary['pass_k'])}")
    print(f"疑似部位命中（对照专家判断，不能用数据验证）{_show(summary['parts_hit_expert'])}")
    LAST_RUN.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细：{LAST_RUN.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
