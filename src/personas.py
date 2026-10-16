"""Persona：同一颗「大脑」（模型），换一套 harness 配置，就是另一个 agent。

一个 persona 是 .agents/personas/<id>.md：frontmatter 写配置，正文就是这个角色的提示词
（替换 system prompt 的第一句 "You are a coding agent..."，其余段落照旧由 prompt.py 按工具拼出来）。

    ---
    name: bajie                       # id：小写字母开头，只含小写字母、数字、连字符；/persona bajie 用它
    title: 猪八戒                     # 展示名，可选，默认等于 id
    description: 只看不改的检查员      # /persona 列表里显示
    emoji: 🐷                         # 可选
    tools: read_file, list_files      # 逗号分隔一行；不写就是纯聊天，一个工具都没有
    ---
    你是猪八戒……

和 skill 用同一种写法，复用 skills.parse_frontmatter（不引入 PyYAML）。它只认顶层单行键值，所以 tools 不能写成列表。
skill 是「一份菜谱」，加载后追加进当前对话；persona 是「换一个厨师」，提示词、工具、对话、记忆整套换掉。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from skills import parse_frontmatter

DEFAULT_PERSONAS_DIR = os.path.join(".agents", "personas")
# default 是没有 persona 时的内置会话；reload 是 /persona 的子命令。这两个名字留给 harness 自己用。
RESERVED_IDS = {"default", "reload"}
_ID = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")


@dataclass(frozen=True)
class Persona:
    id: str
    title: str
    description: str
    tool_names: tuple[str, ...]
    prompt: str  # 正文，替换 system prompt 的第一句
    emoji: str = ""
    path: str = ""  # 文件的绝对路径，/persona reload 时重新读它

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}".strip()


def load_persona(path: str, known_tools: list[str]) -> Persona:
    """读一个 persona 文件；格式不对抛 ValueError（原因写在消息里）。

    known_tools 是 harness 实际有的工具名，按它的顺序排列 persona 的工具：写的顺序、空格、重复都不影响结果。
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    fm = parse_frontmatter(text)
    if fm is None:
        raise ValueError("no YAML frontmatter")
    pid = fm.get("name", "").strip()
    if not _ID.fullmatch(pid) or len(pid) > 64:
        raise ValueError(f"invalid or missing name: {pid!r} (lowercase letters, digits and '-')")
    if pid in RESERVED_IDS:
        raise ValueError(f"name {pid!r} is reserved")
    description = fm.get("description", "").strip()
    if not description:
        raise ValueError("missing description")
    body = _body(text)
    if not body:
        raise ValueError("empty prompt: write the persona's instructions below the frontmatter")

    wanted = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
    unknown = sorted(wanted - set(known_tools))
    if unknown:
        raise ValueError(f"unknown tools: {', '.join(unknown)} (available: {', '.join(known_tools)})")

    return Persona(
        id=pid,
        title=fm.get("title", "").strip() or pid,
        description=description,
        tool_names=tuple(t for t in known_tools if t in wanted),
        prompt=body,
        emoji=fm.get("emoji", "").strip(),
        path=os.path.abspath(path),
    )


def _body(text: str) -> str:
    """frontmatter 之后的正文。parse_frontmatter 已经确认有开头和结尾两行 ---。"""
    lines = text.splitlines()
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    return "\n".join(lines[end + 1 :]).strip()


def discover_personas(directory: str, known_tools: list[str]) -> tuple[list[Persona], list[str]]:
    """读目录下所有 .md，返回 (按 id 排序的 personas, 警告)。目录不存在是正常情况：没有 persona。

    重名时按文件名顺序先到先得。坏文件跳过并给出原因，不让一个写错的文件挡住其他角色。
    """
    if not os.path.isdir(directory):
        return [], []
    found: dict[str, Persona] = {}
    warnings: list[str] = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".md"):
            continue
        path = os.path.join(directory, filename)
        try:
            persona = load_persona(path, known_tools)
        except (OSError, ValueError) as e:
            warnings.append(f"跳过 persona {filename}：{e}")
            continue
        if persona.id in found:
            warnings.append(
                f"忽略重名 persona {persona.id}（{filename}），已使用 {os.path.basename(found[persona.id].path)}"
            )
            continue
        stem = os.path.splitext(filename)[0]
        if stem != persona.id:  # 能用优先，但提醒：/persona 后面要打的是 id，不是文件名
            warnings.append(f"persona 文件名 {stem} 与 name {persona.id} 不一致（切换时用 /persona {persona.id}）")
        found[persona.id] = persona
    return sorted(found.values(), key=lambda p: p.id), warnings
