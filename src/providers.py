"""Provider 层：把「DeepSeek API 的线上格式」和「agent 循环」隔开。

DeepSeek 走 OpenAI 兼容协议（Chat Completions + function calling）。agent.py 只认三样东西：

    Reply(texts, tool_calls, usage)   模型这一轮说了什么 / 想调什么工具 / 花了多少 token
    ToolCall(id, name, input)  一次工具调用（已把 JSON 参数解析成 dict）
    ToolResult(call_id, content, is_error)

协议细节全部收在 DeepSeekProvider 里：
  system prompt   请求时在最前面拼一条 role=system 消息（不存进 conversation）
  工具声明        {"type": "function", "function": {name, description, parameters}}
  assistant 回灌  content + tool_calls（+ reasoning_content：思考模式带 tools 时必须回灌）
  工具结果回灌    每个结果一条 role=tool 消息，tool_call_id 与 tool_calls[i].id 配对
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
    input_error: str | None = None  # 参数解析失败的原因；agent 不执行工具，把它当错误结果回灌


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int  # 这次请求发出去的全部上下文——它随轮数增长，就是上下文管理要管的东西
    output_tokens: int
    cached_tokens: int = 0  # 输入里命中服务端前缀缓存的部分（更便宜）


@dataclass(frozen=True)
class Reply:
    texts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None


class Provider(Protocol):
    """agent 循环依赖的接口；测试里用 fake 实现替换。"""

    label: str  # 终端里打印的名字

    def chat(self, conversation: list[dict], tools: list[Tool], system: str = "") -> Reply:
        """发送 system + 整个 conversation；把 assistant 回复 append 进去；返回归一化的 Reply。
        system 怎么编码是各家协议的事，不存进 conversation。"""

    def tool_results(self, results: list[ToolResult]) -> list[dict]:
        """把一轮的工具结果编码成要 append 进 conversation 的消息列表。"""


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
                raise RuntimeError("请先设置 DEEPSEEK_API_KEY（.env 或 export）；https://platform.deepseek.com")
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

    def chat(self, conversation: list[dict], tools: list[Tool], system: str = "") -> Reply:
        extra: dict[str, Any] = {}
        if tools:  # OpenAI 协议不接受空的 tools 数组
            extra["tools"] = [self._tool_param(t) for t in tools]
        # OpenAI 协议里 system prompt 就是排在最前面的一条 role=system 消息；每次请求现拼，不进 conversation。
        messages = [{"role": "system", "content": system}, *conversation] if system else conversation
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=messages,
            reasoning_effort=self.reasoning_effort,
            **extra,
        )
        message = response.choices[0].message
        conversation.append(self._assistant_message(message))

        texts = [message.content] if message.content else []
        tool_calls = [self._tool_call(tc) for tc in (message.tool_calls or [])]
        return Reply(texts=texts, tool_calls=tool_calls, usage=self._usage(response))

    @staticmethod
    def _usage(response: Any) -> Usage | None:
        u = getattr(response, "usage", None)
        if u is None:
            return None
        # prompt_cache_hit_tokens 是 DeepSeek 的扩展字段：它会自动缓存请求前缀，不用手动打标记。
        return Usage(u.prompt_tokens, u.completion_tokens, getattr(u, "prompt_cache_hit_tokens", 0) or 0)

    @staticmethod
    def _tool_call(tc: Any) -> ToolCall:
        """arguments 是模型生成的 JSON 字符串，可能是坏的。不能抛异常：assistant 消息已经
        append 进 conversation，协议要求每个 tool_call id 都配一条 role=tool 回复。"""
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as e:
            return ToolCall(tc.id, tc.function.name, {}, input_error=f"arguments is not valid JSON: {e}")
        if not isinstance(args, dict):
            return ToolCall(tc.id, tc.function.name, {}, input_error="arguments must be a JSON object")
        return ToolCall(tc.id, tc.function.name, args)

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
