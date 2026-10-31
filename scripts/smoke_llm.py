"""模型接口冒烟测试：只发一次带工具的请求，看这个模型能不能用来跑 agent。

拿到现场账号后先跑它（一分钟内出结果），再决定用哪个模型：
    python scripts/smoke_llm.py

读 .env 里的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。三种结果：
- 通过：模型返回了 tool_call，参数是合法 JSON → 可以跑 agent 循环；
- 不调工具：接口通，但模型直接回答了文字 → 换模型，或者这个模型不支持工具调用；
- 报错：地址、密钥或模型名不对，原样打印错误（不打印密钥）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quality_agent.llm import OpenAICompatLLM  # noqa: E402
from run import load_dotenv  # noqa: E402

PING_TOOL = {
    "type": "function",
    "function": {
        "name": "get_machine",
        "description": "按机器编号查机型和机龄",
        "parameters": {
            "type": "object",
            "properties": {"machine_id": {"type": "integer"}},
            "required": ["machine_id"],
        },
    },
}
MESSAGES = [{"role": "user", "content": "查一下 17 号机器的机型。必须调用工具，不要直接回答。"}]


def main() -> int:
    load_dotenv(ROOT / ".env")
    llm = OpenAICompatLLM.from_env()
    print(f"模型：{llm.model}  地址：{llm.client.base_url}")
    start = time.monotonic()
    try:
        reply = llm.chat(MESSAGES, [PING_TOOL])
    except Exception as exc:  # 冒烟测试要把任何错误都原样告诉人
        print(f"报错（{type(exc).__name__}）：{exc}")
        return 2
    elapsed = time.monotonic() - start

    if not reply.tool_calls:
        print(f"不调工具（{elapsed:.1f}s）：模型直接回答了 → {reply.content!r}")
        return 1
    call = reply.tool_calls[0]
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError:
        print(f"工具参数不是合法 JSON（{elapsed:.1f}s）：{call.arguments!r}")
        return 1
    print(f"通过（{elapsed:.1f}s）：调用 {call.name}({arguments})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
