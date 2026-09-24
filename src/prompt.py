"""System prompt：由运行时状态拼出来，而不是写死的字符串。

参考 Vercel Academy《Build Your Own AI Coding Agent Harness》模块 3：
  prompt 写的是「策略」（该怎么做、有什么界限），「能力」交给工具描述；
  分段：角色 / # Agency / # Guardrails / # Verification / # Project Instructions；
  哪些段出现取决于实际挂载的工具——--step 2 只有 read_file，就不该谈编辑和验证。

build_system_prompt 是纯函数：同样的 PromptContext → 同样的 prompt，没有 I/O，所以能单测。
读 git、读文件这类 I/O 在下面单独的函数里，由 main() 启动时调一次再传进来。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

AGENTS_FILE = "AGENTS.md"
# AGENTS.md 是原样注入 prompt 的外部文本：太长会挤占上下文，而且在谁的目录里启动就信谁的文件，
# 所以设个上限。它能诱导模型做的最危险的事是 run_bash，那一步每条命令都要用户确认。
MAX_PROJECT_CONTEXT_CHARS = 20_000


@dataclass(frozen=True)
class PromptContext:
    working_directory: str
    tool_names: list[str]
    git_branch: str | None = None
    project_context: str | None = None  # AGENTS.md 的内容


def build_system_prompt(ctx: PromptContext) -> str:
    tools = set(ctx.tool_names)
    can_edit = "edit_file" in tools
    can_run = "run_bash" in tools

    sections = [
        (
            "You are a coding agent running in the user's terminal.\n"
            f"Working directory: {ctx.working_directory} (all relative paths are relative to it)."
        )
    ]
    if ctx.git_branch:
        # 只在启动时读一次；agent 自己用 run_bash 切了分支，这行就过期了，所以写明 at startup。
        sections.append(f"Git branch at startup: {ctx.git_branch}")

    agency = [
        "# Agency",
        f"- Available tools: {', '.join(ctx.tool_names)}",
        "- USE your tools: look at the real files instead of guessing, then answer.",
        "- Do NOT explain what you WOULD do. Actually do it.",
        "- If a tool returns an error, read the message and retry with a corrected call instead of giving up.",
    ]
    if can_edit:
        agency.append("- Read a file before editing it.")
    if can_run:
        agency.append("- Prefer read_file / list_files for looking at files; use run_bash for running programs.")
        agency.append("- If the user denies a tool call, don't retry it; ask them what they want instead.")
    agency.append("- Keep replies short and concrete. Reply in the language the user writes in.")
    sections.append("\n".join(agency))

    if can_edit or can_run:
        sections.append(
            "# Guardrails\n"
            "- Prefer simple, minimal changes; don't touch code unrelated to the task.\n"
            "- Look at existing code before creating new files, and reuse existing patterns.\n"
            "- No new dependencies without asking."
        )

    if can_run:
        # 教程 3.3「验证门」：要的不是「全检查」，而是「如实说检查了什么」。
        sections.append(
            "# Verification\n"
            "After making changes, verify your work:\n"
            "1. Run the checks that actually exist in this project (tests, linters, or just running the program).\n"
            "2. Report exactly what you ran and its result, and what you could not run (e.g. the user denied it).\n"
            "3. Do NOT inflate partial verification into a blanket success claim.\n"
            'Do NOT claim "tests pass" or "it works" unless you ran it in this session.'
        )

    if ctx.project_context:
        sections.append(f"# Project Instructions (from AGENTS.md)\n{ctx.project_context}")

    return "\n\n".join(sections)


def current_git_branch(cwd: str) -> str | None:
    """当前分支名；不在 git 仓库里、detached HEAD、或没装 git 都返回 None（这一段就不出现）。"""
    try:
        # --show-current 在还没有任何 commit 的新仓库里也能用；rev-parse --abbrev-ref HEAD 会报错。
        out = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def load_project_context(cwd: str) -> str | None:
    """读工作目录下的 AGENTS.md（项目自己的命令、架构、踩过的坑）；没有或为空返回 None。

    只看 cwd 这一个文件，不往上找父目录、不合并多份——教程 3.4 的最小版本。
    """
    path = os.path.join(cwd, AGENTS_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read().strip()
    if len(content) > MAX_PROJECT_CONTEXT_CHARS:
        dropped = len(content) - MAX_PROJECT_CONTEXT_CHARS
        content = content[:MAX_PROJECT_CONTEXT_CHARS] + f"\n... (truncated {dropped} chars)"
    return content or None
