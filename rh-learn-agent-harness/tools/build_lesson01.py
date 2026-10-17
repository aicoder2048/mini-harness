# /// script
# requires-python = ">=3.12"
# dependencies = ["openai>=1.0", "prompt-toolkit>=3.0.53", "rich>=15.0.0"]
# ///
"""生成第 01 课页面里「真的 harness 输出」的部分，或者检查它们有没有过期：

    uv run rh-learn-agent-harness/tools/build_lesson01.py           # 重新生成 lessons/01-three-agents.html
    uv run rh-learn-agent-harness/tools/build_lesson01.py --check   # 只检查：页面和现在的 harness 不一致就报错

页面由 tools/templates/01-three-agents.html 加上下面两样东西拼成：
  1. 装配台的数据：3 个角色 × 16 种工具组合，每种都用 prompt.build_system_prompt 真的拼一遍，
     按段落切开去重后嵌进页面（页面只负责显示，不重新实现拼装规则）；
  2. 终端输出：用 agent.Agent 真的跑 /tools、/persona 等斜杠命令（provider 是个不会被调用的替身，不花钱），
     截下 stdout。
两者都假设练习文件夹在 EXAMPLE_DIR，所以页面上各处的字符数一致。

改了 .agents/personas/*.md、src/prompt.py、src/agent.py 的显示文字或工具说明之后，跑一次 --check；
报过期就去掉 --check 重新生成。要改课文，改模板，不要直接改 lessons/01-three-agents.html。
依赖和 mini-harness 一样（会 import src/ 里的模块），用 PEP 723 内联声明，由 uv 建隔离环境。
"""

from __future__ import annotations

import contextlib
import html
import io
import itertools
import json
import re
import sys
from pathlib import Path

COURSE = Path(__file__).resolve().parent.parent
REPO = COURSE.parent
TEMPLATE = COURSE / "tools" / "templates" / "01-three-agents.html"
LESSON = COURSE / "lessons" / "01-three-agents.html"
EXAMPLE_DIR = "/Users/xiaoming/xiyou"  # 课里说「下面的例子假设练习文件夹在这里」
TODAY = "2026-10-18"  # 进记忆规则段落的日期；固定下来，页面才不会每天都「过期」
PERSONAS = ["tang", "wukong", "bajie"]

sys.path.insert(0, str(REPO / "src"))

from agent import Agent, persona_session
from personas import discover_personas, load_persona
from prompt import PromptContext, build_system_prompt
from tools import ALL_TOOLS

TOOL_NAMES = [t.name for t in ALL_TOOLS]


def prompt_data() -> dict:
    """每个角色、每种工具组合的系统提示词，切成段落去重。段落顺序：角色正文、Working directory、# 各段。"""
    sections: dict[str, str] = {}
    combos: dict[str, list[str]] = {}

    def section_id(text: str) -> str:
        for key, value in sections.items():
            if value == text:
                return key
        key = f"s{len(sections)}"
        sections[key] = text
        return key

    personas = {}
    for pid in PERSONAS:
        p = load_persona(str(REPO / ".agents" / "personas" / f"{pid}.md"), TOOL_NAMES)
        personas[pid] = {"label": p.label, "tools": list(p.tool_names)}
        for bits in itertools.product([0, 1], repeat=len(TOOL_NAMES)):
            names = [t for t, b in zip(TOOL_NAMES, bits) if b]
            ctx = PromptContext(
                working_directory=EXAMPLE_DIR,
                tool_names=names,
                memory_dir=f"{EXAMPLE_DIR}/Memory/{pid}",
                today=TODAY,
                persona=p.prompt,
            )
            text = build_system_prompt(ctx)
            # 段落之间是 "\n\n"，段内也可能有空行，所以只在 "\n\n# " 处切；第一段再拆出 Working directory 那一行
            parts = text.split("\n\n# ")
            parts = [parts[0]] + ["# " + x for x in parts[1:]]
            body, _, wd = parts[0].rpartition("\nWorking directory:")
            blocks = [body, "Working directory:" + wd] + parts[1:]
            if "\n\n".join([blocks[0] + "\n" + blocks[1]] + blocks[2:]) != text:
                sys.exit(f"切段后拼不回原文（{pid} {names}）：prompt.py 的分段方式变了，要更新本脚本")
            combos[f"{pid}|{''.join(map(str, bits))}"] = [section_id(b) for b in blocks]
    return {"tools": TOOL_NAMES, "personas": personas, "sections": sections, "combos": combos}


class _StubProvider:
    label = "DeepSeek"

    def chat(self, *args, **kwargs):
        raise AssertionError("这里只跑斜杠命令，不该调用模型")


def terminal_output() -> list[str]:
    """和 main() 一样构造会话，跑一串斜杠命令，返回去掉颜色码的 stdout 各行。"""
    memory_root = f"{EXAMPLE_DIR}/Memory"
    base = PromptContext(working_directory=EXAMPLE_DIR, tool_names=TOOL_NAMES, memory_dir=memory_root, today=TODAY)
    personas, warnings = discover_personas(str(REPO / ".agents" / "personas"), TOOL_NAMES)
    if warnings:
        sys.exit("角色文件有问题：\n" + "\n".join(warnings))
    inputs = iter(["/persona", "/persona tang", "/persona wukong", "/persona bajie", "/tools", "/tools read_file"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        Agent(
            _StubProvider(),
            ALL_TOOLS,
            lambda: next(inputs, None),
            system=build_system_prompt(base),
            sessions=[persona_session(p, base, memory_root) for p in personas],
        ).run()
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue()).split("\n")


def _block(lines: list[str], start: str, n: int | None = None, until: str | None = None) -> list[str]:
    try:
        i = next(k for k, line in enumerate(lines) if line.startswith(start))
        if n is not None:
            return lines[i : i + n]
        return lines[i : next(k for k in range(i, len(lines)) if lines[k] == until) + 1]
    except StopIteration:
        sys.exit(f"终端输出里找不到「{start}」：agent.py 的显示文字变了，要更新本脚本")


def build() -> str:
    lines = terminal_output()
    esc = lambda s: html.escape(s, quote=False)
    you = lambda cmd: f'<span class="a-blue">You</span>: {esc(cmd)}'
    read_file = _block(lines, "read_file（发给模型的说明）：", until="}")
    bajie_tools = _block(lines, "🐷 猪八戒 的工具（2 个）：", n=4)
    switch = _block(lines, "🔁 🐒 孙悟空 → 🐷 猪八戒", n=5)
    fills = {
        "__TOOLS_READ_FILE__": "\n".join([you("/tools read_file")] + [esc(x) for x in read_file]),
        "__TOOLS_BAJIE__": "\n".join(
            [you("/tools")]
            + [esc(x) for x in bajie_tools[:-1]]
            + [f'<span class="a-dim">{esc(bajie_tools[-1])}</span>']
        ),
        "__SWITCH_BAJIE__": "\n".join([you("/persona bajie")] + [esc(x) for x in switch]),
        # 嵌在 <script> 里：把 "</" 转义，免得数据里的文字提前结束 script 标签
        "__PROMPT_DATA__": json.dumps(prompt_data(), ensure_ascii=False).replace("</", "<\\/"),
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for marker, value in fills.items():
        if page.count(marker) != 1:
            sys.exit(f"模板里 {marker} 应该正好出现一次")
        page = page.replace(marker, value)
    return page


def main() -> None:
    page = build()
    if "--check" in sys.argv[1:]:
        if LESSON.read_text(encoding="utf-8") != page:
            sys.exit(f"{LESSON.relative_to(REPO)} 过期了：去掉 --check 重新生成（要改课文请改模板）")
        print(f"ok   {LESSON.relative_to(REPO)} 和现在的 harness 一致")
        return
    LESSON.write_text(page, encoding="utf-8")
    print(f"已生成 {LESSON.relative_to(REPO)}")


if __name__ == "__main__":
    main()
