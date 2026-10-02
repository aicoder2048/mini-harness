# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""检查课程页面，不用开浏览器：

    uv run rh-learn-python/tools/check_lessons.py            # 检查全部课
    uv run rh-learn-python/tools/check_lessons.py 05 -v      # 只查第 05 课，并打印每个格子的输出

检查内容：
  1. 每个可运行格子（.cell）用页面里同一套运行器（course.js 的 RUNNER）跑一遍：
     没写 data-expect-error 的不许报错；写了的必须报那个错。
  2. 挑战题：标准答案 + 判分代码必须通过；初始代码 + 判分代码必须不通过（否则题目等于白送）。
  3. 「Found in the robot」摘录必须和 src/ 里的原文逐行一致（data-translated 的只比代码、不比注释）。
  4. 选择题的答案字母在选项范围内；页面里的 id 不重复。
"""

from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import types
from pathlib import Path

COURSE = Path(__file__).resolve().parent.parent
REPO = COURSE.parent
RUNNER_RE = re.compile(r"const RUNNER = String\.raw`(.*?)`; // END RUNNER", re.DOTALL)
CELL_RE = re.compile(r'<div class="cell"([^>]*)>(.*?)</div>', re.DOTALL)
SCRIPT_RE = re.compile(r'<script type="text/x-python"(?: class="(check|solution)")?>(.*?)</script>', re.DOTALL)
FIG_RE = re.compile(r'<figure class="excerpt"([^>]*)>(.*?)</figure>', re.DOTALL)
QUIZ_RE = re.compile(r'<div class="quiz"([^>]*)>(.*?)<ol class="options">(.*?)</ol>', re.DOTALL)
ATTR_RE = re.compile(r'data-([\w-]+)(?:="([^"]*)")?')
ID_RE = re.compile(r'\sid="([^"]+)"')


def dedent(text: str) -> str:
    """和 course.js 的 dedent 一致：去掉首尾空行和公共缩进。"""
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(line[cut:].rstrip() for line in lines)


def attrs(raw: str) -> dict[str, str]:
    return {k: html.unescape(v or "") for k, v in ATTR_RE.findall(raw)}


def load_runner(workdir: Path) -> types.FunctionType:
    """在 workdir 里执行 course.js 中的 Python 运行器，返回 run_cell。输入由 INPUTS 队列提供。"""
    source = RUNNER_RE.search((COURSE / "assets" / "course.js").read_text(encoding="utf-8"))
    if not source:
        sys.exit("course.js 里找不到 RUNNER")
    io_mod = types.ModuleType("teardown_io")
    io_mod.queue = []
    io_mod.next_input = lambda prompt: io_mod.queue.pop(0) if io_mod.queue else None
    sys.modules["teardown_io"] = io_mod
    os.chdir(workdir)
    ns: dict = {"__name__": "teardown_runner"}
    # 故意 exec：要跑的正是页面里那段运行器源码，校验它本身也在校验范围内
    exec(compile(source.group(1), "<exec>", "exec"), ns)  # noqa: S102
    return ns["run_cell"]


def run(run_cell, code: str, check: str | None = None, inputs: list[str] | None = None) -> dict:
    sys.modules["teardown_io"].queue = list(inputs or [])
    return run_cell(code, check)


def strip_comment(line: str) -> str:
    """去掉不在字符串里的 # 注释（摘录的注释可能翻译过）。"""
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 1
            elif line.startswith(quote, i):
                i += len(quote) - 1
                quote = None
        elif line.startswith(('"""', "'''"), i):
            quote = line[i : i + 3]
            i += 2
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def check_page(page: Path, verbose: bool) -> list[str]:
    text = page.read_text(encoding="utf-8")
    problems: list[str] = []
    name = page.name

    ids = ID_RE.findall(text)
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{name}: duplicate id {dup!r}")

    for raw_attrs, _, body in QUIZ_RE.findall(text):
        a = attrs(raw_attrs)
        count = body.count("<li")
        answer = a.get("answer", "")
        if len(answer) != 1 or not ("a" <= answer <= chr(ord("a") + count - 1)):
            problems.append(f"{name}: quiz answer {answer!r} doesn't match its {count} options ({raw_attrs.strip()})")

    for raw_attrs, body in FIG_RE.findall(text):
        a = attrs(raw_attrs)
        scripts = SCRIPT_RE.findall(body)
        if not scripts or "file" not in a:
            problems.append(f"{name}: excerpt without code or data-file")
            continue
        shown = dedent(scripts[0][1]).split("\n")
        src = (REPO / a["file"]).read_text(encoding="utf-8").split("\n")
        start = int(a.get("start", "1"))
        real = src[start - 1 : start - 1 + len(shown)]
        # 页面会去掉公共缩进（方法体里的摘录不必从第 8 列开始），原文也照样去掉再比
        indents = [len(line) - len(line.lstrip(" ")) for line in real if line.strip()]
        cut = min(indents) if indents else 0
        real = [line[cut:] for line in real]
        translated = "translated" in a
        for offset, (s, r) in enumerate(zip(shown, real + [""] * len(shown))):
            if translated:
                s, r = strip_comment(s), strip_comment(r)
            if s.rstrip() != r.rstrip():
                problems.append(
                    f"{name}: excerpt {a['file']} line {start + offset} differs\n      page: {s!r}\n      real: {r!r}"
                )
                break

    cells = CELL_RE.findall(text)
    with tempfile.TemporaryDirectory() as tmp:
        run_cell = load_runner(Path(tmp))
        for n, (raw_attrs, body) in enumerate(cells, 1):
            a = attrs(raw_attrs)
            parts = {kind or "code": dedent(code) for kind, code in SCRIPT_RE.findall(body)}
            code = parts.get("code", "")
            inputs = a["inputs"].split("|") if "inputs" in a else []
            label = f"{name} cell #{n}"
            if "input(" in code and "inputs" not in a:
                print(f"  note {label}: uses input() without data-inputs (browser will ask with a pop-up)")

            res = run(run_cell, code, None, inputs)
            expect = a.get("expect-error")
            if expect:
                if res["ok"] or res["error_type"] != expect:
                    problems.append(f"{label}: expected {expect}, got {res['error_type'] or 'no error'}")
            elif not res["ok"] and "check" not in parts:
                problems.append(f"{label}: {res['error_type']}: {res['error']} (line {res['line']})")
            if verbose:
                print(f"--- {label} output ---\n{res['output']}", end="" if res["output"].endswith("\n") else "\n")
                if not res["ok"]:
                    print(f"[{res['error_type']}: {res['error']}]")

            if "check" in parts:
                if "solution" not in parts:
                    problems.append(f"{label}: challenge has a check but no solution")
                else:
                    sol = run(run_cell, parts["solution"], parts["check"], inputs)
                    if sol["check"] != "pass":
                        why = sol["check_msg"] or f"{sol['error_type']}: {sol['error']}"
                        problems.append(f"{label}: SOLUTION FAILS its check: {why}")
                starter = run(run_cell, code, parts["check"], inputs)
                if starter["check"] == "pass":
                    problems.append(f"{label}: starter code already passes the check")
    os.chdir(REPO)
    count = f"{len(cells)} cells, {len(FIG_RE.findall(text))} excerpts, {len(QUIZ_RE.findall(text))} quizzes"
    print(f"{'FAIL' if problems else 'ok  '} {name}: {count}")
    return problems


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv
    pages = sorted((COURSE / "lessons").glob("*.html"))
    if args:
        pages = [p for p in pages if any(p.name.startswith(a) for a in args)]
    problems = [p for page in pages for p in check_page(page, verbose)]
    for p in problems:
        print("  ✗ " + p)
    print(f"\n{len(pages)} pages checked, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
