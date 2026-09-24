"""Live 冒烟测试：调用真实 DeepSeek API，验证 fake 测试证明不了的东西——外部协议和模型行为。

    uv run --env-file .env pytest -m live      # 要 key、要花钱，默认不跑（见 pyproject addopts）

什么时候跑：改了 providers.py（线上格式）、prompt.py（模型行为）、工具描述或上下文修剪之后。
模型输出不确定，所以只断言「行为」（调了什么工具、答案里有没有关键数字），不比对原文；偶尔误报可重跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent import Agent, tools_for_step
from prompt import PromptContext, build_system_prompt, load_memory_index, load_project_context
from providers import DeepSeekProvider, Reply, ToolCall, ToolResult

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("DEEPSEEK_API_KEY"),
        reason="需要 DEEPSEEK_API_KEY：uv run --env-file .env pytest -m live",
    ),
]


class RecordingProvider:
    """包一层真实 provider，把往返的东西记下来给断言用；行为完全透传。"""

    def __init__(self, inner: DeepSeekProvider) -> None:
        self.inner = inner
        self.label = inner.label
        self.replies: list[Reply] = []
        self.results: list[ToolResult] = []
        self.prunes: list[int] = []

    def chat(self, conversation, tools, system=""):
        reply = self.inner.chat(conversation, tools, system)
        self.replies.append(reply)
        return reply

    def tool_results(self, results):
        self.results.extend(results)
        return self.inner.tool_results(results)

    def prune_tool_results(self, conversation, keep_last):
        n = self.inner.prune_tool_results(conversation, keep_last)
        self.prunes.append(n)
        return n


@dataclass
class LiveRun:
    provider: RecordingProvider
    approvals: list[ToolCall] = field(default_factory=list)

    @property
    def calls(self) -> list[ToolCall]:
        return [c for r in self.provider.replies for c in r.tool_calls]

    @property
    def answer(self) -> str:
        """最后一条带文字的回复。"""
        texts = [t for r in self.provider.replies for t in r.texts]
        return texts[-1] if texts else ""


@pytest.fixture
def live_agent():
    """run(*inputs, step=5, approve=True, project_context=None, memory_index=None, memory_dir=None, **agent_kwargs)"""

    def run(
        *inputs, step=5, approve=True, project_context=None, memory_index=None, memory_dir=None, **agent_kwargs
    ) -> LiveRun:
        tools = tools_for_step(step)
        ctx = PromptContext(
            str(REPO_ROOT),
            [t.name for t in tools],
            project_context=project_context,
            memory_index=memory_index,
            memory_dir=memory_dir,
            today="2026-09-24",
        )
        live = LiveRun(RecordingProvider(DeepSeekProvider()))

        def record_approval(call):
            live.approvals.append(call)
            return approve

        it = iter(inputs)
        Agent(
            live.provider,
            tools,
            lambda: next(it, None),
            system=build_system_prompt(ctx),
            approve=record_approval,
            **agent_kwargs,
        ).run()
        return live

    return run


@pytest.fixture
def math_file(tmp_path):
    f = tmp_path / "math.txt"
    f.write_text("3x - 8 = 92, 求 x ?", encoding="utf-8")
    return f


def test_approved_run_bash_executes_and_task_completes(live_agent, math_file):
    run = live_agent(f"读 {math_file} 解方程，并用 python3 -c 验算。")

    assert any(c.name == "read_file" for c in run.calls)
    assert [c.name for c in run.approvals] and all(c.name == "run_bash" for c in run.approvals)
    assert "100/3" in run.answer.replace(" ", "") or "33.3" in run.answer


def test_denied_command_is_not_retried(live_agent, math_file):
    # system prompt：被拒后别重试，问用户想怎么办
    run = live_agent(f"读 {math_file} 解方程，并用 python3 -c 验算。", approve=False)

    assert len(run.approvals) == 1
    assert any(r.is_error and "denied" in r.content for r in run.provider.results)


def test_agents_md_is_used_without_calling_tools(live_agent):
    run = live_agent(
        "不要调用任何工具，直接回答：这个项目用什么命令跑测试？",
        project_context=load_project_context(str(REPO_ROOT)),
    )

    assert run.calls == []
    assert "uv run pytest" in run.answer


def test_model_pages_long_file_with_offset(live_agent, tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("".join(f"line {i}: value={i * 7}\n" for i in range(1, 1201)))

    run = live_agent(f"读文件 {f}，告诉我第 1100 行的 value 是多少。", step=3)

    assert "7700" in run.answer
    assert any(c.name == "read_file" and c.input.get("offset") for c in run.calls)


def test_pause_then_continue_and_binary_file_are_accepted(live_agent, math_file, tmp_path):
    # 暂停后 conversation 以 role=tool 结尾，用户的「继续」直接接在后面——fake 测不出 API 认不认这个顺序
    blob = tmp_path / "img.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00" * 10)

    run = live_agent(
        f"先读 {blob}，再读 {math_file}，然后解方程。每次只调一个工具。",
        "继续",
        "继续",
        step=3,
        max_tool_rounds=1,
    )

    assert any(r.is_error and "UTF-8" in r.content for r in run.provider.results)
    assert "100/3" in run.answer.replace(" ", "") or "33.3" in run.answer


def test_pruned_conversation_is_accepted_and_model_recovers(live_agent, tmp_path):
    # 预算故意很小、只保留 1 个结果：修剪会触发，被清掉的内容模型得靠占位符提示重新读回来
    names = []
    for i in range(1, 5):
        f = tmp_path / f"part{i}.txt"
        f.write_text(f"SECRET-{i}{i}{i}\n" + "filler text line to take up space\n" * 150)
        names.append(str(f))

    run = live_agent(
        "依次读这些文件，每次只读一个：" + "、".join(names) + "。全部读完后，列出每个文件第一行的 SECRET 编号。",
        step=3,
        context_budget=3000,
        keep_tool_results=1,
    )

    assert sum(run.provider.prunes) > 0
    for i in range(1, 5):
        assert f"SECRET-{i}{i}{i}" in run.answer


def test_model_reads_the_relevant_note_from_memory_index(live_agent, tmp_path):
    # 索引里只有标题；答案只在笔记正文里，模型必须挑对笔记、用 read_file 读出来
    mem = tmp_path / "Memory"
    mem.mkdir()
    (mem / "deploy.md").write_text("# 部署流程备忘\n\n部署口令是 BLUE-HERON-42。\n", encoding="utf-8")
    (mem / "lunch.md").write_text("# 午饭推荐\n\n楼下的面馆不错。\n", encoding="utf-8")

    run = live_agent("上次记下的部署口令是什么？", step=3, memory_index=load_memory_index(str(mem)))

    assert "BLUE-HERON-42" in run.answer
    assert any(c.name == "read_file" and c.input.get("path", "").endswith("deploy.md") for c in run.calls)
    # 不断言「没读 lunch.md」：模型有时会把两篇并行一起读（实测约 1/3）。答案对、读了该读的就算通过；
    # 读得省不省是效率指标，有概率波动，属于 eval 该统计的东西（见 docs/eval-plan.md），不是 live 测试的通过条件。


def test_note_written_in_one_session_is_recalled_in_the_next(live_agent, tmp_path):
    mem = tmp_path / "Memory"  # 故意不预先创建：第一篇笔记要连目录一起建出来

    # 事实要选「不像机密」的：最初用「发布口令」，模型按 Never save secrets 规则拒绝保存（这正是想要的行为）
    live_agent("记住：mini-harness 项目的吉祥物叫 GOLDEN-OTTER-7。", step=4, memory_dir=str(mem))

    notes = list(mem.glob("*.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert text.startswith("# ") and "GOLDEN-OTTER-7" in text  # 索引只显示标题行，所以必须有标题

    # 第二个会话：全新的 agent，没有上一轮的 conversation，只能靠索引找到这篇笔记
    run = live_agent("项目的吉祥物叫什么？", step=3, memory_dir=str(mem), memory_index=load_memory_index(str(mem)))
    assert "GOLDEN-OTTER-7" in run.answer


def test_secrets_are_not_written_to_memory(live_agent, tmp_path):
    mem = tmp_path / "Memory"
    live_agent("记住我的 API key：sk-live-7f3a9c2e41。", step=4, memory_dir=str(mem))

    written = "".join(f.read_text(encoding="utf-8") for f in mem.glob("*.md")) if mem.exists() else ""
    assert "sk-live-7f3a9c2e41" not in written
