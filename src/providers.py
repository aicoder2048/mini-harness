"""Provider 层：把「模型 API 的线上格式」和「agent 循环」隔开。

原教程只有 Anthropic 一家。这里默认改成 DeepSeek（OpenAI 兼容协议），
同时保留 Anthropic 协议作为对照。agent.py 只认三样东西：

    Reply(texts, tool_calls)   模型这一轮说了什么 / 想调什么工具
    ToolCall(id, name, input)  一次工具调用（已把 JSON 参数解析成 dict）
    ToolResult(call_id, content, is_error)

两家协议的差异全部收在各自的 Provider 里：
                    DeepSeek / OpenAI 兼容            Anthropic
  工具声明          {"type":"function","function":…}  {"name","description","input_schema"}
  assistant 回灌    content + tool_calls (+reasoning) content 块列表原样回灌
  工具结果回灌      每个结果一条 role=tool 消息         一条 user 消息，内含 tool_result 块
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from tools import Tool

MAX_TOKENS = 4096  # 输出上限。第 4 步模型要吐整个文件，1024 会截断在半句话上。


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Reply:
    texts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


class Provider(Protocol):
    label: str  # 终端里打印的名字

    def chat(self, conversation: list[dict], tools: list[Tool]) -> Reply:
        """发送整个 conversation；把 assistant 回复按本家格式 append 进去；返回归一化的 Reply。"""

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        """把一轮的工具结果编码成本家格式的消息列表（可能一条，也可能多条）。"""


# --- DeepSeek（OpenAI 兼容协议）------------------------------------------------


class DeepSeekProvider:
    label = "DeepSeek"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str = "low",
        client: Any = None,
    ) -> None:
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.reasoning_effort = reasoning_effort
        if client is None:
            api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("请先 export DEEPSEEK_API_KEY=... （https://platform.deepseek.com）")
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
        self.client = client

    @staticmethod
    def _tool_param(tool: Tool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def chat(self, conversation: list[dict], tools: list[Tool]) -> Reply:
        extra: dict[str, Any] = {}
        if tools:  # OpenAI 协议不接受空的 tools 数组
            extra["tools"] = [self._tool_param(t) for t in tools]
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=conversation,
            reasoning_effort=self.reasoning_effort,
            **extra,
        )
        message = response.choices[0].message
        conversation.append(self._assistant_message(message))

        texts = [message.content] if message.content else []
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, input=json.loads(tc.function.arguments or "{}"))
            for tc in (message.tool_calls or [])
        ]
        return Reply(texts=texts, tool_calls=tool_calls)

    @staticmethod
    def _assistant_message(message: Any) -> dict:
        """显式构造回灌的 dict，而不是直接 append SDK 对象——看得见到底发了什么给模型。"""
        out: dict[str, Any] = {"role": "assistant", "content": message.content}
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            # DeepSeek 思考模式下，只要请求带 tools，之前所有轮的 reasoning_content 都必须原样回灌。
            out["reasoning_content"] = reasoning
        if message.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        return out

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        # OpenAI 协议没有 is_error 字段，用前缀让模型知道这是失败。
        return [
            {
                "role": "tool",
                "tool_call_id": r.call_id,
                "content": f"Error: {r.content}" if r.is_error else r.content,
            }
            for r in results
        ]


# --- Anthropic（原教程协议）------------------------------------------------------


class AnthropicProvider:
    label = "Claude"

    def __init__(self, model: str | None = None, client: Any = None) -> None:
        # 原教程用 claude-sonnet-5。SDK 自己读 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL。
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def chat(self, conversation: list[dict], tools: list[Tool]) -> Reply:
        extra: dict[str, Any] = {}
        if tools:
            extra["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        message = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, messages=conversation, **extra)
        # 整个 content 块列表原样回灌，不要压扁成字符串——tool_use / tool_result 靠这些块配对。
        conversation.append({"role": "assistant", "content": message.content})

        texts = [b.text for b in message.content if b.type == "text"]
        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in message.content if b.type == "tool_use"
        ]
        return Reply(texts=texts, tool_calls=tool_calls)

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        # 一轮里的多个结果必须放在同一条 user 消息里，不能拆成多条。
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                    for r in results
                ],
            }
        ]


# --- 工厂 ---------------------------------------------------------------------

PROVIDERS = {"deepseek": DeepSeekProvider, "anthropic": AnthropicProvider}
DEFAULT_PROVIDER = "deepseek"


def make_provider(name: str = DEFAULT_PROVIDER) -> Provider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ValueError(f"unknown provider: {name!r}; choose from {sorted(PROVIDERS)}") from None
