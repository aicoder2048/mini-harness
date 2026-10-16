"""Agent 循环测试：注入 fake provider 和脚本化输入，只看循环的形状。"""

import os

import pytest

from agent import (
    MAX_TOOL_ROUNDS,
    Agent,
    ConsoleApprover,
    Session,
    _memory,
    _skills,
    command_argument,
    parse_args,
    persona_session,
    slash_command,
    switch_summary,
    tools_for_step,
)
from personas import Persona, discover_personas, load_persona
from prompt import PromptContext
from providers import Reply, ToolCall, ToolResult, Usage
from tools import ALL_TOOLS, Tool, read_file


class FakeProvider:
    label = "Fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.chats: list[list[dict]] = []  # 每次 chat 收到的 conversation 快照
        self.systems: list[str] = []
        self.tool_names: list[list[str]] = []  # 每次 chat 收到的工具名
        self.results: list[list[ToolResult]] = []
        self.prunes: list[int] = []  # 每次 prune_tool_results 收到的 keep_last

    def chat(self, conversation, tools, system=""):
        self.chats.append(list(conversation))
        self.systems.append(system)
        self.tool_names.append([t.name for t in tools])
        reply = self.replies.pop(0)
        conversation.append({"role": "assistant", "content": reply.texts})
        return reply

    def prune_tool_results(self, conversation, keep_last):
        self.prunes.append(keep_last)
        return 1

    def tool_results(self, results):
        self.results.append(results)
        return [{"role": "tool", "content": r.content} for r in results]


def scripted(*inputs):
    it = iter(inputs)
    return lambda: next(it, None)  # 输入用完 → None（相当于 Ctrl-D）


def test_tool_call_loops_back_to_model_without_asking_user(tmp_path, capsys):
    secret = tmp_path / "secret.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["a horse"]),
        ]
    )

    Agent(provider, [read_file], scripted("solve it")).run()

    assert len(provider.chats) == 2  # 只有一次用户输入，却问了模型两次
    assert provider.results == [[ToolResult("c1", "neigh")]]
    assert provider.chats[1][-1] == {"role": "tool", "content": "neigh"}
    assert "a horse" in capsys.readouterr().out


def test_reply_markdown_is_rendered_not_printed_raw(capsys):
    provider = FakeProvider([Reply(texts=["**bold** and `code`"])])
    Agent(provider, [], scripted("hi")).run()
    out = capsys.readouterr().out
    assert "bold" in out and "code" in out
    assert "**" not in out and "`" not in out


def test_token_usage_is_logged_to_stderr_not_stdout(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(4210, 120, 3800))])
    Agent(provider, [], scripted("go")).run()
    out, err = capsys.readouterr()
    assert "in 4,210" in err and "cached 3,800" in err and "out 120" in err
    assert "4,210" not in out


def test_no_usage_line_when_provider_reports_none(capsys):
    Agent(FakeProvider([Reply(texts=["hi"])]), [], scripted("go")).run()
    assert capsys.readouterr().err == ""


def test_prunes_old_tool_results_when_input_exceeds_budget(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1001, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000, keep_tool_results=5).run()
    assert provider.prunes == [5]
    assert "清理" in capsys.readouterr().err


def test_no_pruning_within_budget():
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1000, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000).run()
    assert provider.prunes == []


def test_tool_error_is_fed_back_not_raised(tmp_path):
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "missing")})]),
            Reply(texts=["sorry"]),
        ]
    )

    Agent(provider, [read_file], scripted("go")).run()

    [[result]] = provider.results
    assert result.is_error and result.call_id == "c1"


def test_unknown_tool_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "rm_rf", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "unknown tool" in result.content


def test_missing_argument_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "read_file", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "missing argument" in result.content


def test_input_error_is_an_error_result_and_tool_is_not_run(tmp_path):
    bad = ToolCall("c1", "read_file", {}, input_error="invalid JSON")
    provider = FakeProvider([Reply(tool_calls=[bad]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "invalid JSON" in result.content


def _dangerous_tool(ran: list):
    return Tool("danger", "d", {"type": "object"}, run=lambda args: ran.append(args) or "done", needs_approval=True)


@pytest.mark.parametrize("approved", [True, False])
def test_needs_approval_tool_runs_only_when_user_approves(approved):
    ran, asked = [], []
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "danger", {"x": 1})]), Reply(texts=["ok"])])

    Agent(provider, [_dangerous_tool(ran)], scripted("go"), approve=lambda call: asked.append(call) or approved).run()

    [[result]] = provider.results
    assert [c.id for c in asked] == ["c1"]
    assert ran == ([{"x": 1}] if approved else [])
    assert result.is_error is (not approved)


def test_tools_without_needs_approval_never_ask(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), approve=lambda call: pytest.fail("should not ask")).run()


def _tool_reply(i):
    return Reply(tool_calls=[ToolCall(f"c{i}", "read_file", {"path": "/nonexistent"})])


def test_tool_rounds_cap_pauses_and_hands_back_to_user(capsys):
    provider = FakeProvider([_tool_reply(i) for i in range(10)])

    Agent(provider, [read_file], scripted("go"), max_tool_rounds=3).run()

    assert len(provider.chats) == 3  # 第 3 轮后不再问模型，回到用户（输入已用完 → 退出）
    assert len(provider.results) == 3  # 每轮的工具结果都回灌了，tool_call id 没有落单
    assert "3 轮" in capsys.readouterr().out


def test_tool_rounds_counter_resets_on_user_input():
    replies = [_tool_reply(1), _tool_reply(2), _tool_reply(3), Reply(texts=["done"])]
    provider = FakeProvider(replies)

    Agent(provider, [read_file], scripted("go", "继续"), max_tool_rounds=2).run()

    assert len(provider.chats) == 4  # 没重置的话，第 3 轮之后就又被暂停了
    assert provider.chats[2][-1] == {"role": "user", "content": "继续"}


def test_max_tool_rounds_must_be_positive():
    with pytest.raises(ValueError):
        Agent(FakeProvider([]), [], scripted(), max_tool_rounds=0)


def test_plain_text_reply_returns_to_user_and_keeps_history():
    provider = FakeProvider([Reply(texts=["hi"]), Reply(texts=["bye"])])

    Agent(provider, [], scripted("hello", "", "again")).run()  # 空行被跳过

    assert len(provider.chats) == 2
    assert provider.chats[1][0] == {"role": "user", "content": "hello"}  # 历史全量重发
    assert provider.chats[1][-1] == {"role": "user", "content": "again"}


def test_system_prompt_is_passed_on_every_model_call(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), system="SYS").run()
    assert provider.systems == ["SYS", "SYS"]


def test_eof_exits_without_calling_model():
    provider = FakeProvider([])
    Agent(provider, [], scripted()).run()
    assert provider.chats == []


@pytest.mark.parametrize(
    "step,names",
    [
        (2, ["read_file"]),
        (3, ["read_file", "list_files"]),
        (4, ["read_file", "list_files", "edit_file"]),
        (5, ["read_file", "list_files", "edit_file", "run_bash"]),
    ],
)
def test_tools_for_step(step, names):
    assert [t.name for t in tools_for_step(step)] == names


def test_cli_defaults():
    args = parse_args([])
    assert (args.step, args.max_rounds) == (5, MAX_TOOL_ROUNDS)


def test_cli_max_rounds():
    assert parse_args(["--max-rounds", "5"]).max_rounds == 5


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_cli_rejects_non_positive_max_rounds(bad):
    with pytest.raises(SystemExit):
        parse_args(["--max-rounds", bad])


# --- ConsoleApprover：[Y/n/a] 审批 ---------------------------------------------


def _answers(*answers):
    """脚本化的键盘输入；记录被问了几次。用完抛 EOFError（相当于 Ctrl-D）。"""
    it = iter(answers)
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return read, prompts


def _call(name="run_bash"):
    return ToolCall("c1", name, {"command": "ls"})


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", "  y  "])
def test_approver_enter_or_y_allows_once(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is True


@pytest.mark.parametrize("answer", ["n", "N", "no"])
def test_approver_n_denies(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is False


def test_approver_ctrl_d_denies():
    read, _ = _answers()  # 没有输入 → EOFError
    assert ConsoleApprover(read)(_call()) is False


def test_approver_unrecognized_answer_asks_again():
    read, prompts = _answers("yse", "y")
    assert ConsoleApprover(read)(_call()) is True
    assert len(prompts) == 2


def test_approver_always_stops_asking_for_that_tool_this_session():
    read, prompts = _answers("a")
    approver = ConsoleApprover(read)
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert len(prompts) == 1  # 只问了第一次


def test_approver_always_is_per_tool():
    read, prompts = _answers("a", "n")
    approver = ConsoleApprover(read)
    assert approver(_call("run_bash")) is True
    assert approver(_call("deploy")) is False  # 别的工具照样要问
    assert len(prompts) == 2


def test_each_agent_gets_a_fresh_approver_session():
    # 默认审批器不能是模块级共享对象，否则上一个会话的「a」会带到下一个会话
    a1 = Agent(FakeProvider([]), [], scripted())
    a2 = Agent(FakeProvider([]), [], scripted())
    assert a1.approve is not a2.approve


# --- 记忆目录：默认 <cwd>/Memory ------------------------------------------------


def test_memory_defaults_to_memory_dir_in_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    (tmp_path / "Memory").mkdir()
    (tmp_path / "Memory" / "n.md").write_text("# 项目笔记")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "Memory")
    assert "项目笔记" in index


def test_missing_default_memory_dir_is_silent_but_still_writable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    # 还没记过东西：没有索引，但目录仍然给出——第一篇笔记由 edit_file 连目录一起建出来
    assert _memory(str(tmp_path)) == (str(tmp_path / "Memory"), None)
    assert capsys.readouterr().out == ""


def test_explicit_missing_memory_dir_warns_and_disables_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "nope")
    # 显式配置写错了：提醒，并且不让 agent 往一个拼错的新目录里写
    assert _memory(str(tmp_path)) == (None, None)
    assert "不存在" in capsys.readouterr().out


def test_explicit_relative_memory_dir_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "notes")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "n.md").write_text("# 另一个目录")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "notes") and "另一个目录" in index


# --- 斜杠命令 ------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/exit", "/quit", "  /quit  ", "/EXIT", "/exit now"])
def test_exit_and_quit_end_the_session_without_calling_the_model(text):
    provider = FakeProvider([])
    Agent(provider, [], scripted(text, "never sent")).run()
    assert provider.chats == []


def test_exit_after_some_conversation_stops_there():
    provider = FakeProvider([Reply(texts=["hi"])])
    Agent(provider, [], scripted("hello", "/quit", "never sent")).run()
    assert len(provider.chats) == 1


def test_unknown_command_is_not_sent_to_model_and_lists_commands(capsys):
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/foo", "hello")).run()
    assert provider.chats[0] == [{"role": "user", "content": "hello"}]  # /foo 没进 conversation
    out = capsys.readouterr().out
    assert "/foo" in out and "/exit" in out and "/quit" in out


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/exit", "exit"),
        ("/Quit", "quit"),
        ("/help me", "help"),
        ("/Users/szou/a.py 看看这个", None),  # 路径不是命令：照常发给模型
        ("/", None),
        ("/123", None),
        ("hello /exit", None),
        ("", None),
    ],
)
def test_slash_command_parsing(text, expected):
    assert slash_command(text) == expected


def test_path_starting_with_slash_goes_to_the_model():
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/Users/szou/a.py 看看这个")).run()
    assert provider.chats[0][0]["content"] == "/Users/szou/a.py 看看这个"


# --- skill 目录：默认 <cwd>/skills，另加 MINI_HARNESS_SKILL_DIRS -------------------


def _write_skill(root, name, desc="d"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")
    return d


def test_skills_default_to_project_dot_agents_skills_dir(tmp_path, monkeypatch):
    # 通用约定（Amp、Cline、Warp、Zed、Vercel skills CLI 的 universal）：项目级 .agents/skills/，不绑定某个 agent
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    _write_skill(tmp_path / ".agents" / "skills", "run-checks")
    assert [s.name for s in _skills(str(tmp_path))] == ["run-checks"]


def test_agent_specific_dirs_are_not_scanned_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    _write_skill(tmp_path / ".claude" / "skills", "claude-only")
    _write_skill(tmp_path / "skills", "old-location")
    assert _skills(str(tmp_path)) == []


def test_no_skills_dir_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    assert _skills(str(tmp_path)) == []
    assert capsys.readouterr().out == ""


def test_extra_skill_dirs_are_added_after_project_and_can_be_single_skills(tmp_path, monkeypatch):
    _write_skill(tmp_path / ".agents" / "skills", "run-checks", "project")
    single = _write_skill(tmp_path / "elsewhere", "stock-quote")
    _write_skill(tmp_path / "global", "run-checks", "global")  # 重名：项目内的优先
    monkeypatch.setenv("MINI_HARNESS_SKILL_DIRS", f"{single}:global")  # 相对路径按工作目录解析
    found = {s.name: s.description for s in _skills(str(tmp_path))}
    assert found == {"run-checks": "project", "stock-quote": "d"}


def test_explicit_missing_skill_dir_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_HARNESS_SKILL_DIRS", "nope")
    assert _skills(str(tmp_path)) == []
    assert "不存在" in capsys.readouterr().out


# --- /skill名：用户主动调用 ------------------------------------------------------

from skills import discover_skills


def _skill_set(root, *specs):
    """specs: (name, frontmatter 额外行, 正文)。返回 discover_skills 的结果。"""
    for name, extra, body in specs:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: desc of {name}\n{extra}---\n{body}\n")
    return discover_skills([str(root)])[0]


def test_slash_skill_sends_skill_md_and_request_as_one_user_message(tmp_path):
    skills = _skill_set(tmp_path, ("stock-quote", "", "Run scripts/quote.py SYMBOL."))
    provider = FakeProvider([Reply(texts=["ok"])])

    Agent(provider, [], scripted("/stock-quote TSLA META"), skills=skills).run()

    [message] = provider.chats[0]
    assert message["role"] == "user"
    assert "Run scripts/quote.py SYMBOL." in message["content"]  # SKILL.md 正文
    assert "TSLA META" in message["content"]  # 用户的参数
    assert str(tmp_path / "stock-quote") in message["content"]  # 告诉模型相对路径以哪里为准


def test_slash_skill_works_even_when_model_may_not_invoke_it(tmp_path):
    skills = _skill_set(tmp_path, ("deploy", "disable-model-invocation: true\n", "Deploy steps."))
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/deploy"), skills=skills).run()
    assert "Deploy steps." in provider.chats[0][0]["content"]


def test_user_invocable_false_skill_is_not_a_slash_command(tmp_path, capsys):
    skills = _skill_set(tmp_path, ("background", "user-invocable: false\n", "Background knowledge."))
    provider = FakeProvider([])
    agent = Agent(provider, [], scripted("/background"), skills=skills)
    assert "background" not in agent.commands
    agent.run()
    assert provider.chats == [] and "未知命令" in capsys.readouterr().out


def test_builtin_commands_win_over_skills_with_the_same_name(tmp_path, capsys):
    skills = _skill_set(tmp_path, ("exit", "", "should never load"))
    provider = FakeProvider([])
    Agent(provider, [], scripted("/exit", "never sent"), skills=skills).run()
    assert provider.chats == []
    assert "exit" in capsys.readouterr().out  # 启动时提醒这个 skill 被内置命令遮住了


def test_skills_join_the_completion_menu(tmp_path):
    skills = _skill_set(tmp_path, ("stock-quote", "", "x"))
    agent = Agent(FakeProvider([]), [], scripted(), skills=skills)
    assert {"exit", "quit", "stock-quote"} <= set(agent.commands)
    assert agent.commands["stock-quote"] == "desc of stock-quote"


def test_unreadable_skill_md_is_reported_not_sent(tmp_path, capsys):
    skills = _skill_set(tmp_path, ("gone", "", "x"))
    (tmp_path / "gone" / "SKILL.md").unlink()  # 启动后被删了
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/gone", "hello"), skills=skills).run()
    assert provider.chats[0] == [{"role": "user", "content": "hello"}]
    assert "gone" in capsys.readouterr().out


# --- persona：独立会话与切换 ------------------------------------------------------


def _session(name, tools=(), system=None, label=None, **kw):
    return Session(
        name=name,
        label=label or name,
        description=f"desc of {name}",
        system=system or f"SYS-{name}",
        tools={t.name: t for t in tools},
        **kw,
    )


def test_switching_persona_changes_system_and_tools_sent_to_model():
    provider = FakeProvider([Reply(texts=["a"]), Reply(texts=["b"])])
    sessions = [_session("wukong", [read_file], "SYS-W"), _session("bajie", [], "SYS-B")]
    Agent(provider, [], scripted("hi", "/persona bajie", "hi"), system="SYS-D", sessions=sessions, start="wukong").run()
    assert provider.systems == ["SYS-W", "SYS-B"]
    assert provider.tool_names == [["read_file"], []]


def test_each_persona_keeps_its_own_conversation_and_resumes_it():
    provider = FakeProvider([Reply(texts=["1"]), Reply(texts=["2"]), Reply(texts=["3"])])
    sessions = [_session("a"), _session("b")]
    inputs = scripted("我喜欢太空游戏", "/persona b", "我喜欢什么？", "/persona a", "还记得吗？")
    Agent(provider, [], inputs, sessions=sessions, start="a").run()

    b_first = provider.chats[1]
    assert b_first == [{"role": "user", "content": "我喜欢什么？"}]  # b 看不到 a 说过的话
    a_again = provider.chats[2]
    assert a_again[0] == {"role": "user", "content": "我喜欢太空游戏"}  # 切回 a，接着原来的对话
    assert a_again[-1] == {"role": "user", "content": "还记得吗？"}


def test_persona_command_is_not_sent_to_model():
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/persona b", "hello"), sessions=[_session("b")]).run()
    assert provider.chats == [[{"role": "user", "content": "hello"}]]


def test_always_allow_in_one_persona_does_not_carry_over_to_another():
    ran = []
    danger = _dangerous_tool(ran)
    read_a, prompts_a = _answers("a")
    read_b, prompts_b = _answers("n")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "danger", {})]),
            Reply(texts=["done"]),
            Reply(tool_calls=[ToolCall("c2", "danger", {})]),
            Reply(texts=["denied"]),
        ]
    )
    sessions = [
        _session("a", [danger], approve=ConsoleApprover(read_a)),
        _session("b", [danger], approve=ConsoleApprover(read_b)),
    ]
    Agent(provider, [], scripted("go", "/persona b", "go"), sessions=sessions, start="a").run()
    assert len(prompts_a) == 1 and len(prompts_b) == 1  # b 自己又问了一次
    assert ran == [{}]  # 只有 a 那次真的执行了


def test_persona_list_marks_current_and_includes_default(capsys):
    agent = Agent(FakeProvider([]), [], scripted("/persona"), sessions=[_session("bajie", label="🐷 猪八戒")])
    agent.run()
    out = capsys.readouterr().out
    assert "default" in out and "bajie" in out and "🐷 猪八戒" in out
    current = next(line for line in out.splitlines() if "default" in line)
    assert "→" in current


def test_without_personas_only_default_is_listed(capsys):
    Agent(FakeProvider([]), [], scripted("/persona")).run()
    out = capsys.readouterr().out
    assert "default" in out
    assert "→" in out


def test_unknown_persona_is_reported_with_available_names(capsys):
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/persona nobody", "hi"), system="SYS-D", sessions=[_session("bajie")]).run()
    out = capsys.readouterr().out
    assert "nobody" in out and "bajie" in out and "default" in out
    assert provider.systems == ["SYS-D"]  # 没切走


def test_switching_to_current_persona_says_so(capsys):
    Agent(FakeProvider([]), [], scripted("/persona default")).run()
    assert "已经是" in capsys.readouterr().out


def test_switch_prints_summary(capsys):
    sessions = [_session("wukong", ALL_TOOLS, label="🐒 孙悟空"), _session("bajie", [read_file], label="🐷 猪八戒")]
    Agent(FakeProvider([]), [], scripted("/persona bajie"), sessions=sessions, start="wukong").run()
    out = capsys.readouterr().out
    assert "🐒 孙悟空 → 🐷 猪八戒" in out
    assert "− run_bash" in out


def test_start_must_be_a_known_session():
    with pytest.raises(ValueError, match="nobody"):
        Agent(FakeProvider([]), [], scripted(), start="nobody")


def test_persona_named_default_cannot_replace_builtin_session():
    with pytest.raises(ValueError, match="default"):
        Agent(FakeProvider([]), [], scripted(), sessions=[_session("default")])


def test_agent_properties_follow_current_session():
    agent = Agent(FakeProvider([]), [read_file], scripted(), system="SYS-D", sessions=[_session("b")])
    assert agent.system == "SYS-D" and list(agent.tools) == ["read_file"]
    agent.switch("b")
    assert agent.system == "SYS-b" and agent.tools == {}


# --- switch_summary：切换时打印的对比 ---------------------------------------------


def test_switch_summary_shows_tool_and_section_changes_and_conversation_sizes(tmp_path):
    old = _session("wukong", ALL_TOOLS, "role\n\n# Agency\nx\n\n# Guardrails\ny\n\n# Verification\nz", "🐒 孙悟空")
    old.conversation.extend([{"role": "user", "content": "x"}] * 14)
    new = _session("bajie", ALL_TOOLS[:2], "role\n\n# Agency\nx", "🐷 猪八戒", memory_dir=str(tmp_path / "bajie"))

    text = switch_summary(old, new)

    assert "🐒 孙悟空 → 🐷 猪八戒" in text
    assert "4 → 2" in text and "− edit_file" in text and "− run_bash" in text
    assert "− Guardrails" in text and "− Verification" in text
    assert "0 条" in text and "14 条" in text
    assert str(tmp_path / "bajie") in text


def test_switch_summary_shows_added_tools():
    text = switch_summary(_session("a", [read_file]), _session("b", ALL_TOOLS[:2]))
    assert "1 → 2" in text and "+ list_files" in text


def test_switch_summary_without_memory_says_none():
    assert "记忆" in switch_summary(_session("a"), _session("b"))


# --- persona_session：persona → 会话 ----------------------------------------------


def _persona(pid="bajie", tools=("read_file", "list_files"), prompt="你是猪八戒。"):
    return Persona(id=pid, title="猪八戒", description="只看不改", tool_names=tools, prompt=prompt, emoji="🐷")


def test_persona_session_uses_persona_prompt_tools_and_own_memory_dir(tmp_path):
    base = PromptContext(working_directory="/proj", tool_names=["read_file", "list_files", "edit_file", "run_bash"])
    (tmp_path / "bajie").mkdir()
    (tmp_path / "bajie" / "n.md").write_text("# 小明喜欢太空游戏", encoding="utf-8")
    (tmp_path / "root-note.md").write_text("# 根目录的笔记", encoding="utf-8")

    s = persona_session(_persona(), base, str(tmp_path))

    assert (s.name, s.label, s.description) == ("bajie", "🐷 猪八戒", "只看不改")
    assert list(s.tools) == ["read_file", "list_files"]
    assert s.system.startswith("你是猪八戒。\nWorking directory: /proj")
    assert "Available tools: read_file, list_files" in s.system
    assert "# Verification" not in s.system
    assert s.memory_dir == str(tmp_path / "bajie")
    assert "小明喜欢太空游戏" in s.system and "根目录的笔记" not in s.system


def test_persona_session_without_memory_root_has_no_memory(tmp_path):
    base = PromptContext(working_directory="/proj", tool_names=[], memory_dir="/x", memory_index="- old")
    s = persona_session(_persona(), base, None)
    assert s.memory_dir is None and "# Memory" not in s.system


def test_each_persona_session_gets_its_own_approver():
    base = PromptContext(working_directory="/proj", tool_names=[])
    assert persona_session(_persona("a"), base, None).approve is not persona_session(_persona("b"), base, None).approve


# --- 斜杠命令的参数 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("/persona bajie", "bajie"), ("/persona   bajie  ", "bajie"), ("/persona", ""), ("/tools a b", "a b")],
)
def test_command_argument(text, expected):
    assert command_argument(text) == expected


def test_cli_persona_option():
    assert parse_args([]).persona is None
    assert parse_args(["--persona", "wukong"]).persona == "wukong"


# --- 查看命令：/prompt /tools /status ---------------------------------------------


def _run_and_capture(capsys, inputs, **kw):
    provider = FakeProvider(kw.pop("replies", []))
    agent = Agent(provider, kw.pop("tools", []), scripted(*inputs), **kw)
    agent.run()
    return agent, provider, capsys.readouterr().out


def test_prompt_command_prints_current_system_prompt_and_size(capsys):
    _, _, out = _run_and_capture(capsys, ["/prompt"], system="你是测试用的提示词")
    assert "你是测试用的提示词" in out
    assert "9 字符" in out


def test_prompt_command_follows_persona_switch(capsys):
    sessions = [_session("bajie", system="八戒的提示词")]
    _, _, out = _run_and_capture(capsys, ["/persona bajie", "/prompt"], system="默认提示词", sessions=sessions)
    after = out.split("🔁")[1]
    assert "八戒的提示词" in after and "默认提示词" not in after


def test_tools_command_lists_chinese_summary_and_approval(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools"], tools=ALL_TOOLS)
    assert "读取一个文件的内容" in out  # summary_zh
    bash_line = next(line for line in out.splitlines() if "run_bash" in line)
    assert "需审批" in bash_line
    read_line = next(line for line in out.splitlines() if line.strip().startswith("read_file"))
    assert "需审批" not in read_line


def test_tools_command_for_read_only_persona_has_no_edit_file(capsys):
    sessions = [_session("bajie", ALL_TOOLS[:2])]
    _, _, out = _run_and_capture(capsys, ["/persona bajie", "/tools"], tools=ALL_TOOLS, sessions=sessions)
    after = out.split("🔁")[1].split("\n", 6)[-1]  # 跳过切换对比那几行（里面会提到 − edit_file）
    assert "read_file" in after and "edit_file" not in after


def test_tools_command_with_name_shows_english_description_and_schema(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools read_file"], tools=[read_file])
    assert read_file.description in out
    assert '"required"' in out and '"path"' in out


def test_tools_command_with_unknown_name_is_reported(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools nope"], tools=[read_file])
    assert "nope" in out and "read_file" in out


def test_tools_command_with_no_tools(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools"])
    assert "没有工具" in out


def test_status_command_before_any_model_call(capsys, tmp_path):
    (tmp_path / "bajie").mkdir()
    (tmp_path / "bajie" / "n.md").write_text("# 笔记")
    sessions = [_session("bajie", ALL_TOOLS[:2], label="🐷 猪八戒", memory_dir=str(tmp_path / "bajie"))]
    _, _, out = _run_and_capture(capsys, ["/status"], sessions=sessions, start="bajie")
    assert "🐷 猪八戒" in out and "Fake" in out
    assert "2 个" in out and "read_file, list_files" in out
    assert "0 条" in out
    assert "—" in out  # 还没调用过模型
    assert str(tmp_path / "bajie") in out and "1 条笔记" in out


def test_status_command_shows_last_input_tokens(capsys):
    _, _, out = _run_and_capture(capsys, ["hi", "/status"], replies=[Reply(texts=["ok"], usage=Usage(4210, 10, 3800))])
    status = out.split("角色    ")[-1]  # /status 的第一行起
    assert "4,210" in status and "2 条" in status


def test_status_command_without_memory(capsys):
    _, _, out = _run_and_capture(capsys, ["/status"])
    assert "（无）" in out


@pytest.mark.parametrize("command", ["/prompt", "/tools", "/tools read_file", "/status", "/persona"])
def test_inspection_commands_never_reach_the_model(capsys, command):
    agent, provider, _ = _run_and_capture(capsys, [command], tools=[read_file])
    assert provider.chats == []
    assert agent.current.conversation == []


def test_inspection_commands_join_the_completion_menu():
    agent = Agent(FakeProvider([]), [], scripted())
    assert {"persona", "prompt", "tools", "status"} <= set(agent.commands)


# --- /persona reload：改完 persona 文件不用重启 -------------------------------------


def _reloadable(tmp_path, tools="read_file", body="你是猪八戒。"):
    """一个从文件读出来的 bajie 会话，加上 main() 里同样的 reload 函数。"""
    path = tmp_path / "bajie.md"

    def write(tools, body):
        path.write_text(f"---\nname: bajie\ndescription: d\ntools: {tools}\n---\n{body}\n", encoding="utf-8")

    write(tools, body)
    base = PromptContext(working_directory="/proj", tool_names=[])
    known = [t.name for t in ALL_TOOLS]
    session = persona_session(load_persona(str(path), known), base, None)

    def reload(s):
        return persona_session(load_persona(s.source, known), base, None)

    return session, reload, write


def test_reload_picks_up_new_tools_and_prompt_but_keeps_conversation(tmp_path, capsys):
    session, reload, write = _reloadable(tmp_path)
    provider = FakeProvider([Reply(texts=["1"]), Reply(texts=["2"])])

    def inputs():
        yield "第一句"
        write("read_file, edit_file", "你是改过的猪八戒。")
        yield "/persona reload"
        yield "第二句"

    it = inputs()
    Agent(provider, [], lambda: next(it, None), sessions=[session], start="bajie", reload=reload).run()

    assert provider.tool_names == [["read_file"], ["read_file", "edit_file"]]
    assert "你是改过的猪八戒。" in provider.systems[1]
    assert provider.chats[1][0] == {"role": "user", "content": "第一句"}  # 对话保留
    out = capsys.readouterr().out
    assert "+ edit_file" in out and "缓存" in out


def test_reload_with_broken_file_keeps_old_config(tmp_path, capsys):
    session, reload, write = _reloadable(tmp_path)
    provider = FakeProvider([Reply(texts=["ok"])])

    def inputs():
        write("read_file, fly", "x")  # 不存在的工具
        yield "/persona reload"
        yield "hi"

    it = inputs()
    Agent(provider, [], lambda: next(it, None), sessions=[session], start="bajie", reload=reload).run()
    assert provider.tool_names == [["read_file"]]
    out = capsys.readouterr().out
    assert "fly" in out and "原来的配置" in out


def test_reload_with_deleted_file_keeps_old_config(tmp_path, capsys):
    session, reload, _ = _reloadable(tmp_path)
    (tmp_path / "bajie.md").unlink()
    Agent(FakeProvider([]), [], scripted("/persona reload"), sessions=[session], start="bajie", reload=reload).run()
    assert "原来的配置" in capsys.readouterr().out


def test_reload_rejects_renamed_persona(tmp_path, capsys):
    session, reload, _ = _reloadable(tmp_path)
    path = tmp_path / "bajie.md"
    path.write_text(path.read_text(encoding="utf-8").replace("name: bajie", "name: zhubajie"), encoding="utf-8")
    agent = Agent(FakeProvider([]), [], scripted("/persona reload"), sessions=[session], start="bajie", reload=reload)
    agent.run()
    assert agent.current.name == "bajie"
    assert "zhubajie" in capsys.readouterr().out


def test_reload_unchanged_file_says_so(tmp_path, capsys):
    session, reload, _ = _reloadable(tmp_path)
    Agent(FakeProvider([]), [], scripted("/persona reload"), sessions=[session], start="bajie", reload=reload).run()
    out = capsys.readouterr().out
    assert "没有变化" in out and "缓存" not in out


def test_reload_on_default_session_explains(capsys):
    Agent(FakeProvider([]), [], scripted("/persona reload")).run()
    assert "default" in capsys.readouterr().out


# --- 仓库里的三个西游记角色 ---------------------------------------------------------


def test_journey_to_the_west_personas_load_cleanly():
    root = os.path.join(os.path.dirname(__file__), "..", ".agents", "personas")
    personas, warnings = discover_personas(root, [t.name for t in ALL_TOOLS])
    assert warnings == []
    by_id = {p.id: p for p in personas}
    assert by_id["tang"].tool_names == ("read_file", "list_files", "edit_file")
    assert by_id["wukong"].tool_names == ("read_file", "list_files", "edit_file", "run_bash")
    assert by_id["bajie"].tool_names == ("read_file", "list_files")
    for p in personas:
        assert any("一" <= ch <= "鿿" for ch in p.prompt), p.id  # 正文是中文
