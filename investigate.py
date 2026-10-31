"""命令行：调查一个设备事件，打印调查过程、处置建议和契约检查结果。

    python -m pdm.detection                                   # 先看事件表，挑一个事件
    python investigate.py EV-IM011-0920T23 --mock             # 离线脚本模型
    python investigate.py EV-IM011-0920T23 --mock tool_error  # 失败路径：调查未完成，不出工单
    python investigate.py EV-IM011-0920T23                    # 真实模型（读 .env）
    python investigate.py EV-IM011-0920T23 --as-of "2026-09-21 05:00"

不给 --as-of 时用默认调查时点：持续事件在累计告警满 6 小时时，尖峰在确认孤立时。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from agent.contract import work_order_allowed
from agent.llm import OpenAICompatLLM, load_dotenv
from agent.loop import MAX_TURNS, run_investigation
from agent.mock import SCENARIOS, broken_toolbox, scripted_llm
from agent.toolbox import Toolbox
from pdm.detection import default_as_of, official_detection


def main() -> int:
    parser = argparse.ArgumentParser(description="PdM Agent：调查一个设备事件")
    parser.add_argument("event_id", help="事件编号，如 EV-IM011-0920T23（python -m pdm.detection 可查）")
    parser.add_argument("--as-of", help="调查时点，如 \"2026-09-21 14:00\"；默认见上")
    parser.add_argument("--mock", nargs="?", const="ok", choices=SCENARIOS, help="用离线脚本模型，可选场景")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    args = parser.parse_args()

    detection = official_detection()
    match = detection.events[detection.events["id"] == args.event_id]
    if match.empty:
        raise SystemExit(f"没有事件 {args.event_id}；先运行 python -m pdm.detection 查看事件表")
    as_of = pd.Timestamp(args.as_of) if args.as_of else default_as_of(detection, match.iloc[0].to_dict())

    load_dotenv(Path(__file__).parent / ".env")
    llm = scripted_llm(args.event_id, args.mock) if args.mock else OpenAICompatLLM.from_env()
    toolbox = broken_toolbox(as_of) if args.mock == "tool_error" else Toolbox(as_of)
    result = run_investigation(args.event_id, as_of, llm, toolbox, max_turns=args.max_turns)

    print(f"调查 {args.event_id}，调查时点 {as_of:%Y-%m-%d %H:%M}\n")
    for step in result.trace:
        flag = "（出错）" if "error" in step or "error" in step.get("result", {}) else ""
        dropped = f"（模型自带的 as_of={step['dropped_as_of']} 已丢弃）" if "dropped_as_of" in step else ""
        print(f"[第 {step['turn']} 轮] {step['tool']} {step['arguments']}{flag}{dropped}")
    print()
    print(json.dumps(result.report, ensure_ascii=False, indent=2) if result.report else "（没有得到处置建议）")
    if result.problems:
        print("\n没通过的检查或出错：")
        for problem in result.problems:
            print(f"- {problem}")
    print(f"\n状态：{result.status}")
    if work_order_allowed(result):
        print("工单：可以提交人工审批（在网页事件详情里审批出工单：streamlit run app/main.py）")
    else:
        print(f"工单：不生成（状态为“{result.status}”）")
    return 0 if not result.problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
