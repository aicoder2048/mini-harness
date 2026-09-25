"""Skill：发现、解析、列索引。遵循 Agent Skills 标准（https://agentskills.io）——一个 skill 就是一个目录，
里面有带 YAML frontmatter 的 SKILL.md，可能还有 scripts/、references/。

加载方式是三层渐进式披露（和 Claude Code 一样，也和 Memory 的索引一样）：
  ① 索引：启动时每个 skill 一行「名字: 描述 — 路径」放进 system prompt（skills_index）
  ② 正文：模型判断要用时，自己用 read_file 读 SKILL.md（不需要新工具）
  ③ 资源：SKILL.md 里提到的 scripts/ 用 run_bash 跑，references/ 用 read_file 读
用户也可以 /skill名 主动调用（agent.py）。

frontmatter 只解析用得到的几种写法（已安装的 58 个 skill 里实际出现的：单行、引号、> / >- 折叠块、| 字面块），
不为此引入 PyYAML。认得 Claude Code 的两个调用开关：
  disable-model-invocation: true  → 不进索引，模型不会自己用，只能 /名字 调用
  user-invocable: false           → 不出现在 / 菜单，只给模型用
allowed-tools（免确认授权）故意不支持：skill 是外部文本，不能自己给自己开绿灯。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

SKILL_FILE = "SKILL.md"
# 描述不截短：「什么时候用」通常写在描述后半段（skill-creator 的建议），正是模型选 skill 的依据。
# 上限取标准允许的最大值，只防不合规的超长描述；目录由用户显式挑选，数量有限，总量上限够用。
MAX_DESCRIPTION_CHARS = 1_024
MAX_INDEX_CHARS = 8_000
_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # 标准：小写字母、数字、单个连字符
_KEY = re.compile(r"([A-Za-z][\w-]*):(.*)")
_BLOCK = (">", ">-", ">+", "|", "|-", "|+")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str  # SKILL.md 的绝对路径
    model_invocable: bool = True
    user_invocable: bool = True

    @property
    def directory(self) -> str:
        return os.path.dirname(self.path)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """开头 --- 和下一个 --- 之间的顶层键值；没有 frontmatter 返回 None。嵌套映射（metadata:）的子键忽略。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None

    result: dict[str, str] = {}
    body = lines[1:end]
    i = 0
    while i < len(body):
        m = _KEY.fullmatch(body[i])
        i += 1
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        cont = []  # 后面缩进的续行（块内容、多行纯文本、或嵌套映射）
        while i < len(body) and (not body[i].strip() or body[i][:1] in (" ", "\t")):
            cont.append(body[i].strip())
            i += 1
        if raw in _BLOCK:
            result[key] = _fold(cont) if raw.startswith(">") else "\n".join(cont).strip()
        elif raw == "":
            continue  # 嵌套映射，如 metadata:
        else:
            value = " ".join([raw, *[c for c in cont if c]])
            result[key] = _unquote(value)
    return result


def _fold(lines: list[str]) -> str:
    """> 折叠块：同一段的行用空格连起来，空行变成换行（分段）。"""
    paragraphs, current = [], []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _load(skill_md: str) -> Skill:
    """读一个 SKILL.md；格式不对抛 ValueError（原因写在消息里）。"""
    with open(skill_md, encoding="utf-8", errors="replace") as f:
        fm = parse_frontmatter(f.read())
    if fm is None:
        raise ValueError("no YAML frontmatter")
    name, description = fm.get("name", "").strip(), fm.get("description", "").strip()
    if not _NAME.fullmatch(name) or len(name) > 64:
        raise ValueError(f"invalid or missing name: {name!r}")
    if not description:
        raise ValueError("missing description")
    return Skill(
        name=name,
        description=description,
        path=os.path.abspath(skill_md),
        model_invocable=fm.get("disable-model-invocation", "").lower() != "true",
        user_invocable=fm.get("user-invocable", "").lower() != "false",
    )


def discover_skills(dirs: list[str]) -> tuple[list[Skill], list[str]]:
    """按顺序扫描目录，返回 (skills, 警告)。

    每个目录要么本身就是一个 skill（里面直接有 SKILL.md），要么是 skill 的集合（往下找一层）。
    重名时先找到的生效：项目内的 .agents/skills/ 排在最前，能覆盖外部同名 skill。不存在的目录直接跳过。
    """
    found: dict[str, Skill] = {}
    warnings: list[str] = []
    for root in dirs:
        if not os.path.isdir(root):
            continue
        if os.path.isfile(os.path.join(root, SKILL_FILE)):
            candidates = [os.path.join(root, SKILL_FILE)]
        else:
            candidates = [
                os.path.join(root, d, SKILL_FILE)
                for d in sorted(os.listdir(root))
                if os.path.isfile(os.path.join(root, d, SKILL_FILE))
            ]
        for skill_md in candidates:
            try:
                skill = _load(skill_md)
            except (OSError, ValueError) as e:
                warnings.append(f"跳过 skill {os.path.dirname(skill_md)}：{e}")
                continue
            folder = os.path.basename(skill.directory)
            if skill.name != folder:  # 标准要求一致；仍然加载（能用优先），但提醒
                warnings.append(f"skill 名 {skill.name} 与目录名 {folder} 不一致（Agent Skills 标准要求一致）")
            if skill.name in found:
                warnings.append(
                    f"忽略重名 skill {skill.name}（{skill.directory}），已使用 {found[skill.name].directory}"
                )
                continue
            found[skill.name] = skill
    return sorted(found.values(), key=lambda s: s.name), warnings


def skills_index(skills: list[Skill]) -> str | None:
    """模型可用的 skill，每个一行「- 名字: 描述 — 路径」；描述和总长度都有上限。没有就返回 None。"""
    usable = [s for s in skills if s.model_invocable]
    if not usable:
        return None
    lines: list[str] = []
    used = 0
    for s in usable:
        desc = " ".join(s.description.split())
        if len(desc) > MAX_DESCRIPTION_CHARS:
            desc = desc[:MAX_DESCRIPTION_CHARS] + "…"
        line = f"- {s.name}: {desc} — {s.path}"
        if used + len(line) + 1 > MAX_INDEX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) < len(usable):
        lines.append(f"... ({len(usable) - len(lines)} more skills not listed)")
    return "\n".join(lines)
