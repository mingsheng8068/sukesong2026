"""命令行入口。

离线演示：python run.py C001 --mock
真实模型：在 .env 配好 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 后，python run.py C001
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from quality_agent.llm import OpenAICompatLLM
from quality_agent.loop import MAX_TURNS, run_investigation
from quality_agent.mock import scripted_llm_for_demo


def load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    """读取 .env（只认 KEY=VALUE），不覆盖已有环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="品质客诉调查助手（骨架）")
    parser.add_argument("complaint_id", help="客诉编号，如 C001")
    parser.add_argument("--mock", action="store_true", help="不调真实模型，用预设脚本演示循环")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    args = parser.parse_args()

    load_dotenv()
    llm = scripted_llm_for_demo(args.complaint_id) if args.mock else OpenAICompatLLM.from_env()
    result = run_investigation(args.complaint_id, llm, max_turns=args.max_turns)

    for step in result.trace:
        flag = "（出错）" if "error" in step["result"] else ""
        print(f"[第 {step['turn']} 轮] 调用 {step['tool']} {step['arguments']}{flag}")
    print(json.dumps(result.report, ensure_ascii=False, indent=2) if result.report else "（没有得到调查初稿）")
    if result.problems:
        print("契约检查未通过：")
        for problem in result.problems:
            print(f"- {problem}")
    return 0 if result.report and not result.problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
