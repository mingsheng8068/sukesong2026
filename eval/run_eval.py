"""题集评测：每道题跑 trials 次，统计契约通过率、状态、出处命中和 pass^k。

和具体场景无关：题目用 case_id（旧题集里叫 complaint_id）指向要调查的对象，
现场换成报警题集时只需要换 golden.json 和 --runner。

    python eval/run_eval.py                 # 真实模型（读 .env）
    python eval/run_eval.py --mock          # 离线脚本模型，只检查评测流程本身
    python eval/run_eval.py --only G01 G04  # 只跑部分题

一道题一次运行算“通过”：契约无问题 + 状态在允许范围 + must_cite 全部出现。
pass^k：k 次全部通过才算这道题稳定通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quality_agent.llm import OpenAICompatLLM  # noqa: E402
from quality_agent.loop import run_investigation  # noqa: E402
from quality_agent.mock import scripted_llm_for_demo  # noqa: E402
from run import load_dotenv  # noqa: E402


def score_run(question: dict, result) -> dict:
    report = result.report or {}
    text = json.dumps(report, ensure_ascii=False)
    must = question.get("must_cite", [])
    should = question.get("should_cite_any", [])
    keywords = question.get("cause_keywords_any", [])
    checks = {
        "has_report": bool(result.report),
        "contract_ok": bool(result.report) and not result.problems,
        "status_ok": report.get("status") in question.get("expected_status", []),
        "must_cite_ok": all(ref in text for ref in must),
        "should_cite_hit": (not should) or any(ref in text for ref in should),
        "keyword_hit": (not keywords) or any(word in text for word in keywords),
    }
    checks["passed"] = checks["contract_ok"] and checks["status_ok"] and checks["must_cite_ok"]
    checks["status"] = report.get("status")
    checks["missing_cites"] = [ref for ref in must if ref not in text]
    checks["problems"] = result.problems
    checks["turns"] = result.turns
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="题集评测")
    parser.add_argument("--golden", type=Path, default=ROOT / "eval" / "golden.json")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--trials", type=int, help="覆盖题目里的 trials")
    parser.add_argument("--only", nargs="*", help="只跑这些 qid")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "last_run.json")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    questions = json.loads(args.golden.read_text(encoding="utf-8"))
    if args.only:
        questions = [q for q in questions if q["qid"] in args.only]

    rows = []
    for question in questions:
        case_id = question.get("case_id") or question["complaint_id"]
        trials = args.trials or question.get("trials", 1)
        runs = []
        for _ in range(trials):
            try:
                llm = scripted_llm_for_demo(case_id) if args.mock else OpenAICompatLLM.from_env()
                runs.append(score_run(question, run_investigation(case_id, llm)))
            except (Exception, SystemExit) as exc:  # 一道题崩了（含 mock 没有脚本）不影响其他题，记成失败
                runs.append({"passed": False, "error": f"{type(exc).__name__}: {exc}"})
        passed = sum(run["passed"] for run in runs)
        rows.append({"qid": question["qid"], "case_id": case_id, "passed": passed, "trials": trials, "runs": runs})
        print(f"{question['qid']} {case_id:6s} {passed}/{trials}  状态 {[run.get('status') for run in runs]}")

    total_runs = sum(row["trials"] for row in rows)
    total_passed = sum(row["passed"] for row in rows)
    stable = sum(row["passed"] == row["trials"] for row in rows)
    contract_ok = sum(run.get("contract_ok", False) for row in rows for run in row["runs"])
    print(f"\n单次通过率 {total_passed}/{total_runs}；契约通过 {contract_ok}/{total_runs}；pass^k 稳定通过 {stable}/{len(rows)} 题")

    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细：{args.out.relative_to(ROOT)}")
    return 0 if stable == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
