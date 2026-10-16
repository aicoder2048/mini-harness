"""第 2–5 步：把聊天循环变成 agent。

    uv run src/agent.py --step 2               # + read_file
    uv run src/agent.py --step 3               # + list_files
    uv run src/agent.py --step 4               # + edit_file（原文到此为止）
    uv run src/agent.py --step 5               # + run_bash（每条命令先问你，默认）

整个 agent 就是这一个循环：
  用户输入 → 模型 → 模型说要用工具？→ 执行 → 结果回灌 → 再问模型 → ⋯
                   ↘ 没有工具调用 → 打印答复，回到用户输入

chatbot 和 agent 在代码上的唯一区别，是下面那个 need_user_input 布尔量。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from rich.console import Console
from rich.markdown import Markdown

from cli_input import make_default_reader
from personas import DEFAULT_PERSONAS_DIR, Persona, discover_personas, load_persona
from prompt import (
    AGENTS_FILE,
    PromptContext,
    build_system_prompt,
    current_git_branch,
    load_memory_index,
    load_project_context,
)
from providers import DeepSeekProvider, Provider, ToolCall, ToolResult, Usage
from skills import Skill, discover_skills, skills_index
from tools import ALL_TOOLS, Tool, ToolError, read_file

# 同一次用户输入之后，最多连续几轮「模型要工具 → 执行 → 回灌」。防止模型原地打转、烧 token。
# 到上限就暂停交回给用户；教程（Vercel Academy harness）里对应 stopWhen: stepCountIs(10)。
MAX_TOOL_ROUNDS = 20

# 上下文管理：上一次请求的输入超过预算，就把「最近几个之外」的旧工具结果一次性换成占位符。
# 故意不每轮都修剪：DeepSeek 按前缀自动缓存，改动一条旧消息，它之后的缓存全部失效。
# 超预算才批量清理一次，之后前缀又稳定下来。
CONTEXT_BUDGET = 60_000  # 输入 token
KEEP_TOOL_RESULTS = 5

console = Console()  # 只用来把模型回复渲染成 Markdown；其余输出仍是普通 print


# 斜杠命令：由 harness 自己处理，不发给模型。名字 → 说明（未知命令时列出来）
SLASH_COMMANDS = {
    "exit": "退出本会话和程序",
    "quit": "同 /exit",
    "persona": "列出角色；/persona <名字> 切换角色",
    "prompt": "显示当前角色的完整 system prompt",
    "tools": "列出当前角色的工具；/tools <名字> 看发给模型的说明和参数",
    "status": "一屏看当前角色的 harness 组件：模型、工具、提示词、对话、记忆",
}
_SLASH_COMMAND = re.compile(r"/([A-Za-z][\w-]*)")


def slash_command(text: str) -> str | None:
    """输入是斜杠命令就返回命令名（小写），否则 None。

    只看第一个词，而且整个词必须是 /字母...：这样 `/Users/szou/a.py 看看这个` 这种以路径开头的消息
    不会被当成命令吞掉，照常发给模型。命令后面的参数用 command_argument 取。
    """
    words = text.split()
    if not words:
        return None
    m = _SLASH_COMMAND.fullmatch(words[0])
    return m.group(1).lower() if m else None


def command_argument(text: str) -> str:
    """斜杠命令后面的参数（去掉首尾空格）；没有参数返回空字符串。`/persona bajie` → `bajie`。"""
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


class ConsoleApprover:
    """needs_approval 的工具执行前在终端问 [Y/n/a]。

    回车 / y：允许这一次；n：拒绝；a：本会话内这个工具都不再询问。
    「本会话都允许」的状态存在这个对象里，所以 Agent 只需要 approve(call) -> bool，
    每个 Agent 各自 new 一个，互不串会话。Ctrl-D 视为拒绝：输入已经结束时不该默认放行。
    """

    PROMPT = "\033[95m      允许执行? [Y/n/a]（回车=允许，a=本会话都允许 {tool}）\033[0m "

    def __init__(self, read: Callable[[str], str] = input) -> None:
        self.read = read
        self.always: set[str] = set()  # 本会话内不再询问的工具名

    def __call__(self, call: ToolCall) -> bool:
        if call.name in self.always:
            print(f"\033[2m      （本会话已允许 {call.name}，自动执行）\033[0m")
            return True
        while True:
            try:
                answer = self.read(self.PROMPT.format(tool=call.name)).strip().lower()
            except EOFError:
                return False
            if answer in ("", "y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "all", "always"):
                self.always.add(call.name)
                return True
            # 拼错（比如 yse）既不当允许也不当拒绝，再问一次


DEFAULT_SESSION = "default"  # 没有 persona 时的内置会话：工具由 --step 决定，提示词就是原来那份


@dataclass
class Session:
    """一个角色的全部状态。切换角色 = 换一个 Session；模型（provider）不换。

    system / tools 决定「它是谁、能做什么」；conversation / approve / memory_dir 是它自己的，
    所以切走再切回来能接着聊，「a=本会话都允许」也不会带到别的角色。
    """

    name: str  # persona id，/persona 后面打的名字
    label: str  # 展示名，如「🐷 猪八戒」
    description: str
    system: str
    tools: dict[str, Tool]
    memory_dir: str | None = None
    approve: Callable[[ToolCall], bool] = field(default_factory=ConsoleApprover)
    conversation: list[dict] = field(default_factory=list)
    last_input_tokens: int | None = None  # 上一次请求的输入 token，/status 显示；还没调用过模型是 None
    source: str | None = None  # persona 文件路径，/persona reload 重新读它；default 会话没有


def _sections(system: str) -> list[str]:
    """提示词里的一级标题（# Agency、# Guardrails ...），切换时用来对比少了哪段、多了哪段。"""
    return re.findall(r"^# (.+)$", system, re.MULTILINE)


def _diff(old: list[str], new: list[str]) -> str:
    changes = [f"− {x}" for x in old if x not in new] + [f"+ {x}" for x in new if x not in old]
    return f"（{'  '.join(changes)}）" if changes else ""


def _tools_and_prompt_lines(old: Session, new: Session) -> list[str]:
    return [
        f"  工具    {len(old.tools)} → {len(new.tools)}{_diff(list(old.tools), list(new.tools))}",
        f"  提示词  {len(old.system):,} → {len(new.system):,} 字符{_diff(_sections(old.system), _sections(new.system))}",
    ]


def switch_summary(old: Session, new: Session) -> str:
    """切换角色时打印的对比：哪些 harness 组件变了。模型不变，所以不用列。"""
    return "\n".join(
        [
            f"🔁 {old.label} → {new.label}",
            *_tools_and_prompt_lines(old, new),
            f"  对话    {new.label} 自己的 {len(new.conversation)} 条（{old.label} 的 {len(old.conversation)} 条已保留）",
            f"  记忆    {new.memory_dir or '（无）'}",
        ]
    )


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: list[Tool],
        get_user_input: Callable[[], str | None] | None = None,
        system: str = "",
        approve: Callable[[ToolCall], bool] | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        context_budget: int = CONTEXT_BUDGET,
        keep_tool_results: int = KEEP_TOOL_RESULTS,
        skills: list[Skill] | None = None,
        sessions: list[Session] | None = None,
        start: str = DEFAULT_SESSION,
        reload: Callable[[Session], Session] | None = None,
    ) -> None:
        if max_tool_rounds < 1:
            raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
        self.provider = provider
        # tools / system / approve 这几个参数描述的是内置的 default 会话；sessions 是另外的角色（persona）
        default = Session(
            name=DEFAULT_SESSION,
            label=DEFAULT_SESSION,
            description="内置 coding agent，工具由 --step 决定",
            system=system,
            tools={t.name: t for t in tools},
            approve=approve or ConsoleApprover(),  # 每个 Agent 一个新会话，不共享「a」的状态
        )
        self.sessions: dict[str, Session] = {DEFAULT_SESSION: default}
        for session in sessions or []:
            if session.name in self.sessions:
                raise ValueError(f"session name {session.name!r} is already taken")
            self.sessions[session.name] = session
        if start not in self.sessions:
            raise ValueError(f"unknown start session {start!r}; available: {', '.join(self.sessions)}")
        self.current = self.sessions[start]
        self.reload = reload  # 按 session.source 重新读 persona 文件，返回新会话；读不了就抛 OSError / ValueError
        # 用户能用 /名字 调用的 skill：user-invocable: false 的只给模型用；和内置命令重名的被内置命令遮住
        self.skills: dict[str, Skill] = {}
        for skill in skills or []:
            if not skill.user_invocable:
                continue
            if skill.name in SLASH_COMMANDS:
                print(f"\033[93mskill {skill.name} 与内置命令 /{skill.name} 重名，/{skill.name} 仍是内置命令\033[0m")
                continue
            self.skills[skill.name] = skill
        # 斜杠命令表（补全菜单、未知命令提示都用它）：内置命令 + 可调用的 skill
        self.commands = {**SLASH_COMMANDS, **{name: s.description for name, s in self.skills.items()}}
        # 默认读终端（多行、方向键、历史），每个 Agent 一个，历史不跨会话；测试里注入脚本化输入
        self.get_user_input = get_user_input or make_default_reader(self.commands)
        self.max_tool_rounds = max_tool_rounds
        # 只在终端显示、不发给模型的命令：参数是命令后面的文字
        self._local_commands: dict[str, Callable[[str], None]] = {
            "persona": self._persona_command,
            "prompt": self._show_prompt,
            "tools": self._show_tools,
            "status": self._show_status,
        }
        self.context_budget = context_budget
        self.keep_tool_results = keep_tool_results

    # 下面三个都跟着当前角色走：切换角色后，发给模型的提示词、工具和审批都换成那个角色的
    @property
    def tools(self) -> dict[str, Tool]:
        return self.current.tools

    @property
    def system(self) -> str:
        return self.current.system

    @property
    def approve(self) -> Callable[[ToolCall], bool]:
        return self.current.approve

    def switch(self, name: str) -> None:
        self.current = self.sessions[name]

    def _persona_command(self, arg: str) -> None:
        """/persona：列出角色；/persona <名字>：切换并打印对比。只在终端显示，不发给模型。"""
        if arg.lower() == "reload":
            self._reload_persona()
            return
        if not arg:
            print("角色（/persona <名字> 切换，/persona reload 重新读当前角色的文件）：")
            for s in self.sessions.values():
                mark = "→" if s is self.current else " "
                who = f"{s.label}：" if s.label != s.name else ""
                print(f"  {mark} {s.name:<10} {who}{s.description}（{len(s.tools)} 个工具）")
            return
        target = self.sessions.get(arg.lower())
        if target is None:
            print(f"\033[91m未知角色 {arg}\033[0m。可用：{'  '.join(self.sessions)}")
            return
        if target is self.current:
            print(f"已经是 {target.label} 了")
            return
        print(switch_summary(self.current, target))
        self.switch(target.name)

    def _reload_persona(self) -> None:
        """/persona reload：重新读当前角色的文件，换掉提示词和工具，保留对话、审批状态。

        文件被删、改坏、改了 name：都保留原来的配置并说明原因——改错一个字不该让正在上的课中断。
        """
        old = self.current
        if old.source is None or self.reload is None:
            print(f"{old.label} 不是从 persona 文件读出来的，没有可以重新加载的。先 /persona <名字> 切到一个角色。")
            return
        try:
            fresh = self.reload(old)
        except (OSError, ValueError) as e:
            print(f"\033[91m重新加载失败，继续用原来的配置\033[0m：{e}")
            return
        if fresh.name != old.name:
            print(f"\033[91m文件里的 name 改成了 {fresh.name}，继续用原来的配置\033[0m：改名要重启程序。")
            return
        # 换的是「它是谁、能做什么」，「它经历过什么」留着
        fresh.conversation = old.conversation
        fresh.approve = old.approve
        fresh.last_input_tokens = old.last_input_tokens
        self.sessions[old.name] = fresh
        self.current = fresh
        if (fresh.system, list(fresh.tools)) == (old.system, list(old.tools)):
            print(f"♻️ {fresh.label} 的文件没有变化")
            return
        print(f"♻️ 重新加载 {fresh.label}")
        print("\n".join(_tools_and_prompt_lines(old, fresh)))
        print(f"  对话    保留 {len(fresh.conversation)} 条")
        if fresh.system != old.system:
            print("\033[2m  提示词变了：下一次请求，DeepSeek 的缓存要从头算一次（看 cached 那个数字）\033[0m")

    def _show_prompt(self, arg: str) -> None:
        """/prompt：原样打印当前角色的 system prompt。学生能看到「角色正文 + 按工具拼出来的段落」。"""
        print(self.system or "（空）")
        print(f"\033[2m— {self.current.label} 的 system prompt，共 {len(self.system):,} 字符\033[0m")

    def _show_tools(self, arg: str) -> None:
        """/tools：中文一行说明（只给人看）；/tools <名字>：真正发给模型的英文 description 和参数 schema。"""
        if not self.tools:
            print(f"{self.current.label} 没有工具：只能聊天，不能动手。")
            return
        if arg:
            tool = self.tools.get(arg)
            if tool is None:
                print(f"\033[91m{self.current.label} 没有工具 {arg}\033[0m。有：{'  '.join(self.tools)}")
                return
            print(f"{tool.name}（发给模型的说明）：\n{tool.description}\n\n参数 schema：")
            print(json.dumps(tool.input_schema, ensure_ascii=False, indent=2))
            return
        print(f"{self.current.label} 的工具（{len(self.tools)} 个）：")
        for tool in self.tools.values():
            approval = "  \033[95m[需审批]\033[0m" if tool.needs_approval else ""
            print(f"  {tool.name:<11} {tool.summary_zh}{approval}")
        print("\033[2m/tools <名字> 看发给模型的英文说明和参数\033[0m")

    def _show_status(self, arg: str) -> None:
        """/status：一屏看 harness 的几个组件。模型那一行提醒：所有角色共用同一颗大脑。"""
        s = self.current
        tokens = f"{s.last_input_tokens:,} token" if s.last_input_tokens is not None else "—（还没调用过模型）"
        memory = f"{s.memory_dir}（{_count_notes(s.memory_dir)} 条笔记）" if s.memory_dir else "（无）"
        print(
            "\n".join(
                [
                    f"角色    {s.label}（{s.name}）",
                    f"模型    {self.provider.label}（所有角色共用）",
                    f"工具    {len(s.tools)} 个：{', '.join(s.tools) or '（无）'}",
                    f"提示词  {len(s.system):,} 字符（/prompt 查看）",
                    f"对话    {len(s.conversation)} 条消息",
                    f"上下文  上次请求输入 {tokens}",
                    f"记忆    {memory}",
                ]
            )
        )

    def _execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用。失败也返回结果，交给模型自己纠错。"""
        print(f"\033[92mtool\033[0m: {call.name}({json.dumps(call.input, ensure_ascii=False)})")
        tool = self.tools.get(call.name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool: {call.name}")
            if call.input_error:
                raise ToolError(call.input_error)
            if tool.needs_approval and not self.approve(call):
                raise ToolError("the user denied this tool call")
            return ToolResult(call.id, tool.run(call.input))
        except ToolError as e:
            error = str(e)
        except KeyError as e:  # 模型漏传了必填参数
            error = f"missing argument: {e}"
        print(f"\033[91m      → error\033[0m: {error}")
        return ToolResult(call.id, error, is_error=True)

    @staticmethod
    def _skill_message(skill: Skill, user_input: str) -> str | None:
        """/skill名 [参数]：把 SKILL.md 正文和用户参数合成一条 user 消息（和 Claude Code 一样，之后一直留在对话里）。

        正文用 read_file 读，沿用它的行数 / 字符上限。读不到就提示并返回 None（不发给模型）。
        """
        try:
            body = read_file.run({"path": skill.path})
        except ToolError as e:
            print(f"\033[91m读取 skill {skill.name} 失败：{e}\033[0m")
            return None
        parts = user_input.strip().split(maxsplit=1)
        request = parts[1] if len(parts) > 1 else "(no extra input: follow the skill's default workflow)"
        print(f"\033[2m· 已加载 skill {skill.name}\033[0m")
        return (
            f'The user invoked the skill "{skill.name}". Follow its SKILL.md below. '
            f"Paths in it are relative to {skill.directory}.\n\n"
            f'<skill name="{skill.name}" path="{skill.path}">\n{body}\n</skill>\n\n'
            f"User request: {request}"
        )

    @staticmethod
    def _log_usage(usage: Usage) -> None:
        """每次调模型后一行灰字，打到 stderr：不和回复混在一起。看 in 的数字怎么随轮数涨，就是上下文管理的起点。"""
        line = f"· in {usage.input_tokens:,} (cached {usage.cached_tokens:,}) · out {usage.output_tokens:,}"
        print(f"\033[2m{line}\033[0m", file=sys.stderr)

    def run(self) -> None:
        print(f"Chat with {self.provider.label} — {len(self.tools)} 个工具可用（/exit、/quit 或 Ctrl-D 退出）")
        if self.current.name != DEFAULT_SESSION:
            print(f"当前角色：{self.current.label}（/persona 查看全部角色）")

        need_user_input = True
        tool_rounds = 0  # 自上次用户输入以来连续的工具轮数
        while True:
            conversation = self.current.conversation  # 每轮重新取：/persona 切换后就是另一个角色的对话
            if need_user_input:
                user_input = self.get_user_input()
                if user_input is None:
                    break
                if not user_input:
                    continue
                command = slash_command(user_input)
                if command in ("exit", "quit"):
                    break
                if command in self._local_commands:
                    self._local_commands[command](command_argument(user_input))
                    continue
                if command in self.skills:
                    message = self._skill_message(self.skills[command], user_input)
                    if message is None:
                        continue
                    conversation.append({"role": "user", "content": message})
                elif command is not None:
                    available = "  ".join(f"/{name}" for name in self.commands)
                    print(f"\033[91m未知命令 /{command}\033[0m。可用：{available}（输入 / 看说明）")
                    continue
                else:
                    conversation.append({"role": "user", "content": user_input})
                tool_rounds = 0

            # provider.chat 会把 assistant 回复按本家格式 append 进 conversation。
            reply = self.provider.chat(conversation, list(self.tools.values()), self.system)

            if reply.usage:
                self._log_usage(reply.usage)
                self.current.last_input_tokens = reply.usage.input_tokens
                if reply.usage.input_tokens > self.context_budget:
                    pruned = self.provider.prune_tool_results(conversation, self.keep_tool_results)
                    if pruned:
                        print(
                            f"\033[2m· 上下文超过 {self.context_budget:,} token 预算，清理了 {pruned} 个旧工具结果\033[0m",
                            file=sys.stderr,
                        )
            # 有角色时标签写成「🐒 孙悟空 · DeepSeek」：换的是角色，大脑还是同一个
            speaker = self.provider.label
            if self.current.name != DEFAULT_SESSION:
                speaker = f"{self.current.label} · {speaker}"
            for text in reply.texts:
                print(f"\033[93m{speaker}\033[0m:")
                console.print(Markdown(text))  # Markdown 按块渲染，所以标签单独一行
            results = [self._execute(call) for call in reply.tool_calls]

            if not results:
                need_user_input = True  # 没工具调用 → 回到用户
                continue

            conversation.extend(self.provider.tool_results(results))
            tool_rounds += 1
            if tool_rounds >= self.max_tool_rounds:
                # 结果已经回灌，每个 tool_call id 都有回复；用户下一句（比如「继续」）接在 tool 消息后面即可。
                print(f"\033[91m已连续 {tool_rounds} 轮工具调用，先暂停交回给你；回复「继续」让它接着做。\033[0m")
                need_user_input = True
                continue
            need_user_input = False  # 有工具调用 → 不等用户，直接再问模型


def _count_notes(memory_dir: str) -> int:
    """记忆目录里有几篇笔记（.md，含子目录，跳过隐藏目录）；目录还不存在就是 0。和 load_memory_index 的口径一致。"""
    count = 0
    for _, dirnames, filenames in os.walk(memory_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        count += sum(f.endswith(".md") for f in filenames)
    return count


def tools_for_step(step: int) -> list[Tool]:
    """2=read_file, 3=+list_files, 4=+edit_file, 5=+run_bash"""
    return ALL_TOOLS[: step - 1]


def _positive_int(text: str) -> int:
    value = int(text)  # 非数字抛 ValueError，argparse 会报成 invalid value
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="跟学版 code-editing agent")
    p.add_argument(
        "--step",
        type=int,
        choices=[2, 3, 4, 5],
        default=5,
        help="2=read_file, 3=+list_files, 4=+edit_file, 5=+run_bash",
    )
    p.add_argument(
        "--persona",
        help=f"启动时使用的角色（{DEFAULT_PERSONAS_DIR}/<名字>.md）；不写就是内置的 {DEFAULT_SESSION}",
    )
    p.add_argument(
        "--max-rounds",
        type=_positive_int,
        default=MAX_TOOL_ROUNDS,
        help=f"一次用户输入后最多连续几轮工具调用，到了暂停交回给你（默认 {MAX_TOOL_ROUNDS}）",
    )
    return p.parse_args(argv)


DEFAULT_MEMORY_DIR = "Memory"  # 记忆放在项目下的 Memory/（已在 .gitignore，不进仓库），普通 .md 文件，不依赖第三方工具


def _memory(cwd: str) -> tuple[str | None, str | None]:
    """返回 (记忆目录, 索引)。目录：MINI_HARNESS_MEMORY_DIR，不设则用 <cwd>/Memory；相对路径按工作目录解析，支持 ~。

    默认目录还不存在是正常情况（还没记过东西）：静默，照样给出目录，第一篇笔记由 edit_file 连目录一起建出来。
    显式设置却不存在，说明配置写错了：提醒，并且整个关掉，免得 agent 往一个拼错的新目录里写。
    """
    setting = os.environ.get("MINI_HARNESS_MEMORY_DIR")
    memory_dir = os.path.normpath(os.path.join(cwd, os.path.expanduser(setting or DEFAULT_MEMORY_DIR)))
    if not os.path.isdir(memory_dir):
        if setting:
            print(f"\033[91mMINI_HARNESS_MEMORY_DIR 指向的目录不存在：{memory_dir}（本次不启用记忆）\033[0m")
            return None, None
        return memory_dir, None
    index = load_memory_index(memory_dir)
    if index:
        count = sum(line.startswith("- ") for line in index.splitlines())
        print(f"已加载记忆索引：{count} 条（{memory_dir}）")
    return memory_dir, index


# 项目级 skill 的通用位置（Amp、Cline、Warp、Zed、Vercel skills CLI 的 universal 都用它），不绑定某个 agent；
# .claude/skills 是 Claude Code 自己的目录，这里不读。
DEFAULT_SKILLS_DIR = os.path.join(".agents", "skills")


def _skills(cwd: str) -> list[Skill]:
    """默认只扫 <cwd>/.agents/skills；MINI_HARNESS_SKILL_DIRS（: 分隔）显式追加，每项可以是 skill 集合目录，也可以是单个 skill。

    项目目录排最前：重名时项目内的 skill 生效。相对路径按工作目录解析，支持 ~。
    """
    extra = [p.strip() for p in os.environ.get("MINI_HARNESS_SKILL_DIRS", "").split(os.pathsep) if p.strip()]
    dirs = [os.path.normpath(os.path.join(cwd, os.path.expanduser(p))) for p in [DEFAULT_SKILLS_DIR, *extra]]
    for d in dirs[1:]:
        if not os.path.isdir(d):
            print(f"\033[91mMINI_HARNESS_SKILL_DIRS 里的目录不存在：{d}（跳过）\033[0m")
    skills, warnings = discover_skills(dirs)
    for w in warnings:
        print(f"\033[93m{w}\033[0m")
    if skills:
        print(f"已加载 {len(skills)} 个 skill：{', '.join(s.name for s in skills)}")
    return skills


def _personas(cwd: str) -> list[Persona]:
    """只扫 <cwd>/.agents/personas。坏文件跳过并提醒；没有这个目录就是没有角色，和以前一样。"""
    personas, warnings = discover_personas(os.path.join(cwd, DEFAULT_PERSONAS_DIR), [t.name for t in ALL_TOOLS])
    for w in warnings:
        print(f"\033[93m{w}\033[0m")
    if personas:
        print(f"已加载 {len(personas)} 个角色：{'、'.join(p.label for p in personas)}（/persona 切换）")
    return personas


def persona_session(persona: Persona, base: PromptContext, memory_root: str | None) -> Session:
    """persona → 会话。base 是 default 会话的 PromptContext（工作目录、git、AGENTS.md、skills 都共用），
    换掉的是：角色正文、工具、记忆（<记忆根目录>/<id>/，各记各的）。
    """
    tools = [t for t in ALL_TOOLS if t.name in persona.tool_names]
    memory_dir = os.path.join(memory_root, persona.id) if memory_root else None
    ctx = replace(
        base,
        tool_names=[t.name for t in tools],
        persona=persona.prompt,
        memory_dir=memory_dir,
        memory_index=load_memory_index(memory_dir) if memory_dir else None,
    )
    return Session(
        name=persona.id,
        label=persona.label,
        description=persona.description,
        system=build_system_prompt(ctx),
        tools={t.name: t for t in tools},
        memory_dir=memory_dir,
        source=persona.path,
    )


def main() -> None:
    args = parse_args()

    cwd = os.getcwd()
    tools = tools_for_step(args.step)
    memory_dir, memory_index = _memory(cwd)
    skills = _skills(cwd)
    project_context = load_project_context(cwd)
    if project_context:
        print(f"已加载 {AGENTS_FILE}（{len(project_context)} 字符）作为项目指令")
    ctx = PromptContext(
        working_directory=cwd,
        tool_names=[t.name for t in tools],
        git_branch=current_git_branch(cwd),
        project_context=project_context,
        memory_dir=memory_dir,
        memory_index=memory_index,
        today=datetime.now(UTC).astimezone().date().isoformat(),  # 本机时区的今天
        skills_index=skills_index(skills),
    )
    personas = _personas(cwd)
    if args.persona and args.persona not in {p.id for p in personas} | {DEFAULT_SESSION}:
        available = ", ".join([DEFAULT_SESSION, *(p.id for p in personas)])
        sys.exit(f"没有角色 {args.persona}（可用：{available}）")
    known_tools = [t.name for t in ALL_TOOLS]
    Agent(
        DeepSeekProvider(),
        tools,
        system=build_system_prompt(ctx),
        max_tool_rounds=args.max_rounds,
        skills=skills,
        sessions=[persona_session(p, ctx, memory_dir) for p in personas],
        start=args.persona or DEFAULT_SESSION,
        reload=lambda s: persona_session(load_persona(s.source, known_tools), ctx, memory_dir),
    ).run()


if __name__ == "__main__":
    main()
