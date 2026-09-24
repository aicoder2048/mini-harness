"""Provider 层测试：注入 fake client，只验证「发出去的格式」和「回灌的格式」。"""

from types import SimpleNamespace as NS

import pytest

from providers import DeepSeekProvider, Reply, ToolCall, ToolResult, Usage
from tools import read_file


class FakeOpenAI:
    """记录 chat.completions.create 收到的参数，返回预设消息。"""

    def __init__(self, message, usage=None):
        self.calls: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))
        self._message = message
        self._usage = usage

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(choices=[NS(message=self._message)], usage=self._usage)


def _message(**overrides):
    base = {"content": None, "reasoning_content": None, "tool_calls": None}
    base.update(overrides)
    return NS(**base)


def test_declares_tools_in_openai_function_wrapper():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [read_file])

    assert client.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": read_file.description,
                "parameters": read_file.input_schema,
            },
        }
    ]


def test_omits_tools_param_when_no_tools():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert "tools" not in client.calls[0]


def test_system_prompt_is_sent_first_but_not_stored_in_conversation():
    client = FakeOpenAI(_message(content="hi"))
    conversation = [{"role": "user", "content": "x"}]

    DeepSeekProvider(client=client, model="m").chat(conversation, [], system="be brief")

    assert client.calls[0]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "x"},
    ]
    assert conversation == [{"role": "user", "content": "x"}, {"role": "assistant", "content": "hi"}]


def test_no_system_message_when_system_prompt_empty():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert client.calls[0]["messages"][0]["role"] == "user"


def test_normalizes_reply_and_appends_assistant_with_reasoning():
    msg = _message(
        content="let me look",
        reasoning_content="thinking...",
        tool_calls=[NS(id="call_1", function=NS(name="read_file", arguments='{"path": "a.txt"}'))],
    )
    client = FakeOpenAI(msg)
    conversation = [{"role": "user", "content": "x"}]

    reply = DeepSeekProvider(client=client, model="m").chat(conversation, [read_file])

    assert reply == Reply(texts=["let me look"], tool_calls=[ToolCall("call_1", "read_file", {"path": "a.txt"})])
    assert conversation[-1] == {
        "role": "assistant",
        "content": "let me look",
        "reasoning_content": "thinking...",  # 思考模式 + tools 时必须原样回灌
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
        ],
    }


@pytest.mark.parametrize("arguments", ['{"path": "a.txt"', "[1, 2]", "null"])
def test_malformed_arguments_become_input_error_instead_of_raising(arguments):
    msg = _message(tool_calls=[NS(id="call_1", function=NS(name="read_file", arguments=arguments))])

    reply = DeepSeekProvider(client=FakeOpenAI(msg), model="m").chat([], [read_file])

    [call] = reply.tool_calls
    assert call.id == "call_1"  # id 保留，才能配对回一条 role=tool 的错误结果
    assert call.input == {}
    assert call.input_error


def test_tool_results_become_role_tool_messages():
    provider = DeepSeekProvider(client=FakeOpenAI(_message()), model="m")
    out = provider.tool_results([ToolResult("c1", "file body"), ToolResult("c2", "no such file", is_error=True)])
    assert out == [
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "tool", "tool_call_id": "c2", "content": "Error: no such file"},
    ]


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider()


def test_model_defaults_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert DeepSeekProvider(client=FakeOpenAI(_message())).model == "deepseek-v4-pro"


def test_usage_is_normalized_including_deepseek_cache_hits():
    # 字段名取自真实 DeepSeek 响应：prompt_cache_hit_tokens 是它自动前缀缓存命中的部分
    usage = NS(prompt_tokens=938, completion_tokens=33, prompt_cache_hit_tokens=768)
    reply = DeepSeekProvider(client=FakeOpenAI(_message(content="hi"), usage), model="m").chat([], [])
    assert reply.usage == Usage(input_tokens=938, output_tokens=33, cached_tokens=768)


def test_missing_usage_is_none():
    reply = DeepSeekProvider(client=FakeOpenAI(_message(content="hi")), model="m").chat([], [])
    assert reply.usage is None
