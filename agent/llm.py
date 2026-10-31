"""模型接口：OpenAI 兼容客户端，加一个离线脚本模型用于演示和测试。

移植自赛前骨架 quality_agent/llm.py（D25）；切片 1 加了 load_dotenv、configured_model，供命令行和网页共用。
换模型只改 .env 里的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，循环代码不动。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Union


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


ENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


def load_dotenv(path) -> None:
    """读取 .env（只认 KEY=VALUE），不覆盖已有环境变量。"""
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def configured_model() -> str | None:
    """三项配置都非空才算配好了真实模型，返回模型名；否则 None。只看配置，不发请求。"""
    values = [os.environ.get(key, "").strip() for key in ENV_KEYS]
    return values[2] if all(values) else None


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 只有调真实模型时才需要装 openai

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    @classmethod
    def from_env(cls) -> "OpenAICompatLLM":
        missing = [name for name in ENV_KEYS if not os.environ.get(name, "").strip()]
        if missing:
            raise SystemExit(f"缺少环境变量：{', '.join(missing)}（参考 .env.example），或加 --mock 离线演示")
        return cls(*(os.environ[name].strip() for name in ENV_KEYS))

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        response = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools)
        message = response.choices[0].message
        calls = [
            ToolCall(call.id, call.function.name, call.function.arguments)
            for call in (message.tool_calls or [])
        ]
        return LLMReply(message.content, calls)


Step = Union[LLMReply, Callable[[list[dict]], LLMReply]]


class ScriptedLLM:
    """按预设脚本逐步回复；某一步可以是函数，根据当前对话生成回复。"""

    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.index = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        if self.index >= len(self.steps):
            raise RuntimeError("脚本已用完")
        step = self.steps[self.index]
        self.index += 1
        return step(messages) if callable(step) else step
