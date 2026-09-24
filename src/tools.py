"""工具定义与四个内置工具：read_file / list_files / edit_file / run_bash。
（run_bash 是原文之外加的第 5 步。）

对应 PDF 第七章 tools.py。这一层与模型 Provider 无关：Tool 只描述
name / description / input_schema / run 四要素，具体发给哪家 API、
用什么 JSON 外壳，由 providers.py 负责包装。

原文用 Go 的结构体 + 反射生成 JSON Schema；某些 Python 移植版写成
`from jsonschema import generate_schema`——那个函数并不存在。这里直接手写
schema：零额外依赖，也更透明——模型看到的本来就只是这段 JSON。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]
    needs_approval: bool = False  # True → agent 执行前先问用户（run_bash 这类能动整台机器的工具）


class ToolError(Exception):
    """工具执行失败。会被包成 is_error 的结果回灌给模型，让它自己纠错。"""


# --- read_file ---------------------------------------------------------------

# 工具输出一旦进了 conversation，之后每一轮都要重发。所以每个工具都有上限，
# 并且截断时明确告诉模型「还有多少、怎么看剩下的」——悄悄截断比不截断更糟。
MAX_READ_LINES = 500
MAX_READ_CHARS = 50_000  # 兜底：500 行但每行巨长（压缩过的 JS）


def _read_text(path: str) -> str:
    """read_file 和 edit_file 共用：读 UTF-8 文本，失败一律变成 ToolError。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ToolError(str(e)) from e
    except UnicodeDecodeError as e:
        # 不是 OSError，漏接的话一张图片就能让整个 agent 崩掉。
        raise ToolError(f"{path} is not a UTF-8 text file (binary?): {e.reason} at byte {e.start}") from e


def _positive_int(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError(f"{key} must be a positive integer, got {value!r}")
    return value


def _read_file(args: dict[str, Any]) -> str:
    offset = _positive_int(args, "offset", 1)  # 从 1 开始的行号
    limit = min(_positive_int(args, "limit", MAX_READ_LINES), MAX_READ_LINES)
    lines = _read_text(args["path"]).splitlines(keepends=True)
    total = len(lines)
    if offset > max(total, 1):
        raise ToolError(f"offset {offset} is past the end of the file ({total} lines)")

    end = min(offset - 1 + limit, total)
    text = "".join(lines[offset - 1 : end])
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n... (truncated {len(text) - MAX_READ_CHARS} chars)"
    if offset == 1 and end == total:
        return text  # 整个文件一页读完：原样返回
    note = f"\n... (showing lines {offset}-{end} of {total}"
    if end < total:
        note += f"; call read_file with offset={end + 1} to read more"
    return text + note + ")"


read_file = Tool(
    name="read_file",
    description=(
        "Read the contents of a given relative file path. "
        "Use this when you want to see what's inside a file. "
        "Do not use this with directory names. "
        f"Returns at most {MAX_READ_LINES} lines per call; page through longer files with offset and limit."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The relative path of a file in the working directory.",
            },
            "offset": {"type": "integer", "description": "Optional 1-based line number to start from. Default 1."},
            "limit": {"type": "integer", "description": f"Optional max lines to return (<= {MAX_READ_LINES})."},
        },
        "required": ["path"],
    },
    run=_read_file,
)


# --- list_files --------------------------------------------------------------

_PRUNED_DIRS = {".git", "__pycache__", ".venv", "venv"}
MAX_LIST_ENTRIES = 500  # 带 node_modules 的项目能列出几万项


def _list_files(args: dict[str, Any]) -> str:
    root = args.get("path") or "."
    try:
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # 原地修改 dirnames 是 os.walk 的剪枝约定；写成 dirnames = [...] 无效。
            dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir != ".":
                out.append(rel_dir + "/")
            for name in filenames:
                out.append(name if rel_dir == "." else os.path.join(rel_dir, name))
    except OSError as e:
        raise ToolError(str(e)) from e
    entries = sorted(out)
    shown = json.dumps(entries[:MAX_LIST_ENTRIES], ensure_ascii=False)
    if len(entries) > MAX_LIST_ENTRIES:
        shown += f"\n... (showing {MAX_LIST_ENTRIES} of {len(entries)} entries; pass a more specific path)"
    return shown


list_files = Tool(
    name="list_files",
    description=(
        "List files and directories at a given path. "
        "If no path is provided, lists files in the current directory. "
        "Directories are returned with a trailing slash. "
        f"Returns at most {MAX_LIST_ENTRIES} entries."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional relative path. Defaults to the current directory.",
            }
        },
        "required": [],
    },
    run=_list_files,
)


# --- edit_file ---------------------------------------------------------------


def _edit_file(args: dict[str, Any]) -> str:
    path, old, new = args["path"], args["old_str"], args["new_str"]
    if old == new:
        raise ToolError("old_str and new_str must differ")

    if old == "":  # 空 old_str 约定为「新建文件」
        # 原文没做这个检查。不做的话模型传个空 old_str 就能把整个已有文件清掉重写。
        if os.path.exists(path) and os.path.getsize(path) > 0:
            raise ToolError(f"{path} already exists; use a non-empty old_str to edit it")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return f"Successfully created file {path}"

    content = _read_text(path)
    occurrences = content.count(old)
    if occurrences == 0:
        raise ToolError("old_str not found in file")
    if occurrences > 1:
        # 原文没做这个检查。不做的话模型一次替换会命中多处，改坏文件还沉默通过。
        raise ToolError(f"old_str matched {occurrences} times; make it unique")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new))
    return "OK"


edit_file = Tool(
    name="edit_file",
    description=(
        "Make edits to a text file.\n\n"
        "Replaces 'old_str' with 'new_str' in the given file. "
        "'old_str' and 'new_str' MUST be different from each other, and 'old_str' "
        "must match exactly one place in the file.\n\n"
        "If the file specified with path doesn't exist and 'old_str' is empty, "
        "it will be created. An empty 'old_str' never overwrites a non-empty existing file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The path to the file"},
            "old_str": {
                "type": "string",
                "description": "Text to search for - must match exactly and appear exactly once",
            },
            "new_str": {"type": "string", "description": "Text to replace old_str with"},
        },
        "required": ["path", "old_str", "new_str"],
    },
    run=_edit_file,
)


# --- run_bash ----------------------------------------------------------------

BASH_TIMEOUT = 30  # 秒
MAX_OUTPUT_CHARS = 10_000  # 超长输出会塞爆上下文；保留结尾，因为报错、测试失败、堆栈都在最后


def _run_bash(args: dict[str, Any]) -> str:
    command = args["command"]
    try:
        # start_new_session：命令跑在独立进程组里，超时能连孙进程一起杀；
        # 只杀 sh 的话，孙进程还握着 stdout 管道，communicate() 会一直等下去。
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
        )
    except OSError as e:
        raise ToolError(str(e)) from e
    try:
        raw, _ = proc.communicate(timeout=BASH_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise ToolError(f"command timed out after {BASH_TIMEOUT}s") from None

    output = raw.decode("utf-8", errors="replace")
    if len(output) > MAX_OUTPUT_CHARS:
        output = (
            f"... (truncated: showing last {MAX_OUTPUT_CHARS} of {len(output)} chars)\n" + output[-MAX_OUTPUT_CHARS:]
        )
    if proc.returncode != 0:
        raise ToolError(f"exit code {proc.returncode}\n{output}")
    return output or "(no output)"


run_bash = Tool(
    name="run_bash",
    description=(
        "Run a shell command in the working directory and return its combined stdout and stderr. "
        "Use this to run programs and tests, or for things the other tools can't do. "
        f"Commands time out after {BASH_TIMEOUT} seconds, so don't start servers or interactive programs. "
        f"Output longer than {MAX_OUTPUT_CHARS} characters is cut to its last {MAX_OUTPUT_CHARS} characters. "
        "The user must approve every command before it runs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run, e.g. 'node fizzbuzz.js'"},
        },
        "required": ["command"],
    },
    run=_run_bash,
    needs_approval=True,
)


ALL_TOOLS = [read_file, list_files, edit_file, run_bash]
