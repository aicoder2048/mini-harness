/* Agent Teardown: shared behaviour for every page.

   Page markup this script understands (inside <main class="lesson">):
     <div class="cell" [data-inputs="a|b"] [data-label="..."] [data-expect-error="NameError"]>
        <script type="text/x-python">code</script>
        [<script type="text/x-python" class="check">asserts; may read __output__ (what the
           program printed) and __source__ (the student's code)</script>]
        [<script type="text/x-python" class="solution">solution</script>]
     </div>
     <div class="snippet" [data-highlight="2,4-5"]><script type="text/x-python">code</script></div>
     <figure class="excerpt" data-file="src/x.py" data-start="12" [data-highlight="14"] [data-translated]>
        <script type="text/x-python">code</script><figcaption>...</figcaption></figure>
     <div class="quiz" id="q-.." data-answer="b"><p class="q">..</p><ol class="options"><li>..</li></ol>
        <div class="explain">..</div></div>
     <section class="challenge" id="ch-..">.. a .cell with a check script ..</section>
     <p class="takeaway">一句话要点</p>        (end of a step; CSS adds the 「记住」 label)
     <details class="more"><summary>多了解一点：…</summary>…</details>   (optional extra, closed)

   Every top-level <section> of <main class="lesson"> is one step and should start with an <h2>
   (its text becomes the step's name). Step mode shows one step per screen; 「全部展开」 shows all.

   Python runs in the browser with Pyodide (real CPython 3.12, downloaded from jsdelivr the
   first time a cell runs). Each run gets a fresh namespace; files written to the virtual
   disk stay until the page is reloaded. */

(function () {
  "use strict";

  const PYODIDE_URL = "https://cdn.jsdelivr.net/npm/pyodide@0.27.8/";
  const STORE_KEY = "agent-teardown-progress-v1";
  const THEME_KEY = "agent-teardown-theme";

  const UNITS = {
    1: "通电",
    2: "记忆",
    3: "工具",
    4: "接线",
    5: "总装",
  };

  const LESSONS = [
    { id: "00", file: "00-welcome.html", title: "认识机器人", unit: 1 },
    { id: "01", file: "01-variables.html", title: "变量", unit: 1 },
    { id: "02", file: "02-strings.html", title: "字符串与颜色", unit: 1 },
    { id: "03", file: "03-decisions.html", title: "做判断", unit: 1 },
    { id: "04", file: "04-while-loops.html", title: "while 循环", unit: 1 },
    { id: "05", file: "05-lists.html", title: "列表", unit: 2 },
    { id: "06", file: "06-dictionaries.html", title: "字典、元组与集合", unit: 2 },
    { id: "07", file: "07-for-loops.html", title: "for 循环", unit: 2 },
    { id: "08", file: "08-functions.html", title: "函数", unit: 3 },
    { id: "09", file: "09-errors.html", title: "错误与异常", unit: 3 },
    { id: "10", file: "10-files.html", title: "文件与外部世界", unit: 3 },
    { id: "11", file: "11-modules.html", title: "模块与包", unit: 4 },
    { id: "12", file: "12-classes.html", title: "类与对象", unit: 4 },
    { id: "13", file: "13-the-big-loop.html", title: "大循环", unit: 5 },
    { id: "14", file: "14-testing.html", title: "测试", unit: 5 },
  ];

  // ---------------------------------------------------------------- theme
  // Runs immediately (this file is loaded in <head>) so the page doesn't flash.
  function storedTheme() {
    try {
      const t = localStorage.getItem(THEME_KEY);
      return t === "dark" || t === "light" ? t : null;
    } catch (e) {
      return null;
    }
  }
  function applyTheme(t) {
    if (t) document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
  }
  function effectiveTheme() {
    const t = document.documentElement.dataset.theme;
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyTheme(storedTheme());

  // ---------------------------------------------------------------- progress
  function loadProgress() {
    try {
      const p = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      return p && typeof p === "object" ? p : {};
    } catch (e) {
      return {};
    }
  }
  function saveProgress(p) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(p));
    } catch (e) {
      /* progress is a convenience; the page works without it */
    }
  }
  function record(p, id) {
    if (!p[id] || typeof p[id] !== "object") p[id] = {};
    p[id].done = p[id].done || {};
    return p[id];
  }
  function lessonId() {
    return document.body.dataset.lesson || null;
  }
  function markItem(itemId) {
    const id = lessonId();
    if (!id || !itemId) return;
    const p = loadProgress();
    record(p, id).done[itemId] = true;
    saveProgress(p);
    updateFinish();
  }

  // ---------------------------------------------------------------- helpers
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  }
  function dedent(text) {
    const lines = text.replace(/\r\n?/g, "\n").split("\n");
    while (lines.length && !lines[0].trim()) lines.shift();
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    const indents = lines.filter((l) => l.trim()).map((l) => l.match(/^[ \t]*/)[0].length);
    const cut = indents.length ? Math.min(...indents) : 0;
    return lines.map((l) => l.slice(cut).replace(/\s+$/, "")).join("\n");
  }
  function codeOf(container, selector) {
    const s = container.querySelector(selector);
    return s ? dedent(s.textContent) : null;
  }
  function parseLineSet(spec) {
    const set = new Set();
    if (!spec) return set;
    for (const part of spec.split(",")) {
      const [a, b] = part.trim().split("-").map(Number);
      if (!a) continue;
      for (let i = a; i <= (b || a); i++) set.add(i);
    }
    return set;
  }
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  // ---------------------------------------------------------------- Python syntax highlighting
  const KEYWORDS = new Set(
    ("and as assert async await break class continue def del elif else except finally for from global " +
      "if import in is lambda nonlocal not or pass raise return try while with yield match case").split(" ")
  );
  const CONSTS = new Set(["True", "False", "None"]);
  const BUILTINS = new Set(
    ("print input len range int str float bool list dict set tuple type isinstance sorted sum min max " +
      "enumerate zip open abs round any all next iter super object map filter reversed repr hasattr getattr " +
      "setattr id hash format chr ord frozenset Exception BaseException ValueError KeyError TypeError IndexError " +
      "NameError ZeroDivisionError OSError EOFError FileNotFoundError TimeoutError RuntimeError AssertionError " +
      "SystemExit StopIteration UnicodeDecodeError KeyboardInterrupt PermissionError IsADirectoryError " +
      "NotImplementedError AttributeError ImportError ModuleNotFoundError").split(" ")
  );
  const TOKEN_RE =
    /([rRbBfFuU]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(#[^\n]*)|(@[A-Za-z_][\w.]*)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?j?\b)|([A-Za-z_]\w*)/g;

  function wrapTok(cls, text) {
    // one span per line, so no span ever crosses a newline (line numbers stay simple)
    return text
      .split("\n")
      .map((part) => (part ? `<span class="tok-${cls}">${esc(part)}</span>` : ""))
      .join("\n");
  }
  function highlight(src) {
    let out = "";
    let last = 0;
    let prev = "";
    let m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(src))) {
      if (m[0] === "") {
        TOKEN_RE.lastIndex++;
        continue;
      }
      out += esc(src.slice(last, m.index));
      const t = m[0];
      let cls = null;
      if (m[1] !== undefined) cls = "str";
      else if (m[2] !== undefined) cls = "com";
      else if (m[3] !== undefined) cls = "dec";
      else if (m[4] !== undefined) cls = "num";
      else if (prev === "def" || prev === "class") cls = "fn";
      else if (CONSTS.has(t)) cls = "const";
      else if (KEYWORDS.has(t)) cls = "kw";
      else if (t === "self") cls = "self";
      else if (BUILTINS.has(t)) cls = "bi";
      prev = m[5] !== undefined ? t : "";
      out += cls ? wrapTok(cls, t) : esc(t);
      last = m.index + t.length;
    }
    return out + esc(src.slice(last));
  }

  function renderCodeBlock(code, { start = 1, numbered = false, hl = new Set() } = {}) {
    const pre = el("pre", "code" + (numbered ? " numbered" : ""));
    const lines = highlight(code).split("\n");
    pre.innerHTML = lines
      .map((html, i) => {
        const n = start + i;
        const num = numbered ? `<span class="ln">${n}</span>` : "";
        const body = num + (html || " ");
        return hl.has(n) ? `<span class="hl-line">${body}</span>` : body;
      })
      .join("\n");
    return pre;
  }

  // ---------------------------------------------------------------- ANSI terminal colors
  const ANSI_COLORS = {
    31: "a-red", 91: "a-red",
    32: "a-green", 92: "a-green",
    33: "a-yellow", 93: "a-yellow",
    34: "a-blue", 94: "a-blue",
    35: "a-magenta", 95: "a-magenta",
    36: "a-cyan", 96: "a-cyan",
    90: "a-dim",
  };
  function ansiToHtml(text) {
    let html = "";
    let color = null;
    const style = new Set();
    let echo = false;
    let last = 0;
    let m;
    const re = /\x1b\[([0-9;]*)m|\x01|\x02|\x1b\[[0-9;?]*[A-Za-z]/g;
    const flush = (s) => {
      if (!s) return;
      const cls = [...style];
      if (color) cls.push(color);
      if (echo) cls.push("input-echo");
      html += cls.length ? `<span class="${cls.join(" ")}">${esc(s)}</span>` : esc(s);
    };
    while ((m = re.exec(text))) {
      flush(text.slice(last, m.index));
      last = re.lastIndex;
      if (m[0] === "\x01") echo = true;
      else if (m[0] === "\x02") echo = false;
      else if (m[1] !== undefined) {
        const codes = m[1] === "" ? [0] : m[1].split(";").map(Number);
        for (const c of codes) {
          if (c === 0) {
            color = null;
            style.clear();
          } else if (c === 1) style.add("a-bold");
          else if (c === 2) style.add("a-dim");
          else if (c === 3) style.add("a-italic");
          else if (c === 4) style.add("a-underline");
          else if (c === 22) {
            style.delete("a-bold");
            style.delete("a-dim");
          } else if (c === 39) color = null;
          else if (ANSI_COLORS[c]) color = ANSI_COLORS[c];
        }
      }
    }
    flush(text.slice(last));
    return html;
  }
  function stripAnsi(s) {
    return String(s).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
  }

  // ---------------------------------------------------------------- friendly error tips
  const TIPS = {
    NameError:
      "Python 不认识这个名字。检查拼写（Python 区分大小写），并确认在这一行之前已经创建了这个变量或函数。",
    SyntaxError:
      "Python 读不懂这一行的语法。找找是不是少了冒号 <code>:</code>、引号或括号。有时候真正的错误在上一行。",
    IndentationError:
      "行首的空格不对。<code>if</code>、<code>while</code>、<code>for</code>、<code>def</code> 里面的每一行都要缩进同样的距离（标准是 4 个空格）。",
    TabError: "这一行混用了 Tab 和空格。只用空格就好（这个编辑器里按 Tab 键会打出 4 个空格）。",
    TypeError:
      "用了类型不对的值，比如把数字和文字相加，或者调用函数时参数个数不对。用 <code>str()</code> 和 <code>int()</code> 可以转换类型。",
    KeyError: "字典里没有这个键。检查拼写，或者用 <code>.get(键, 默认值)</code>，它不会出错。",
    IndexError:
      "你要的位置不存在。第一个元素是 <code>[0]</code>，最后一个是 <code>[-1]</code>，只有 3 个元素的列表没有 <code>[3]</code>。",
    ValueError: "类型对了，但值不对，比如 <code>int(\"hello\")</code>。",
    ZeroDivisionError: "你除以了 0。没有哪台计算机能做到。",
    AttributeError:
      "这个值没有这个方法或属性。检查拼写，再用 <code>type(x)</code> 看看这个值到底是什么类型。",
    RanTooLong:
      "程序运行太久，被停下来了。找找有没有条件永远不会变成 <code>False</code> 的 <code>while</code> 循环，或者漏写了 <code>break</code>。",
    EOFError:
      "程序调用了 <code>input()</code>，但已经没有输入了。在真正的终端里，按 <kbd>Ctrl</kbd>+<kbd>D</kbd> 就是这个效果。",
    FileNotFoundError: "这个路径上没有文件。是不是还没创建？检查文件名和所在的文件夹。",
    AssertionError: "<code>assert</code> 失败了：你断言一定成立的事情，实际上不成立。",
    UnboundLocalError: "在函数里给一个变量赋值之前就用了它。先给它赋值，或者把它当作参数传进来。",
    RecursionError: "一个函数不停地调用自己，停不下来。",
    ModuleNotFoundError:
      "Python 找不到这个名字的模块。检查拼写。从网上安装的包（比如 <code>openai</code>）在这个浏览器版 Python 里没有安装。",
    ImportError: "模块存在，但里面没有你要的这个名字。检查 <code>import</code> 后面的拼写。",
    UnicodeDecodeError: "这个文件不是普通的 UTF-8 文本（可能是图片之类的二进制文件）。",
    FrozenInstanceError:
      "这个对象是用 <code>@dataclass(frozen=True)</code> 创建的，创建之后字段就不能再改。需要的话就新建一个对象。",
    ToolError: "这是程序自己定义的错误类型（<code>class ToolError(Exception)</code>）。读一读它的消息，里面写了出了什么问题。",
  };

  // ---------------------------------------------------------------- the Python runner (runs inside Pyodide)
  // Kept as plain Python so tools can run it outside the browser too (see verify script).
  const RUNNER = String.raw`
import builtins, importlib, io, linecache, os, sys, traceback
import teardown_io

_HOME = os.getcwd()
_EVENT_LIMIT = 3_000_000  # roughly a few seconds of work; stops endless loops


class RanTooLong(BaseException):
    """BaseException, so a student's 'except Exception' can't swallow it."""


class _Tee(io.TextIOBase):
    def __init__(self):
        self.transcript = []  # what a terminal would show, including what was typed
        self.program = []  # only what the program printed (checks look at this)

    def writable(self):
        return True

    def write(self, s):
        s = str(s)
        self.transcript.append(s)
        self.program.append(s)
        return len(s)

    def echo(self, s):
        self.transcript.append("\x01" + s + "\x02\n")


_tee = None


def _input(prompt=""):
    prompt = str(prompt)
    sys.stdout.write(prompt)
    line = teardown_io.next_input(prompt)
    if not isinstance(line, str):
        if _tee is not None:
            _tee.echo("^D")
        raise EOFError("EOF when reading a line")
    if _tee is not None:
        _tee.echo(line)
    return line


def _make_tracer():
    count = 0

    def tracer(frame, event, arg):
        nonlocal count
        count += 1
        if count > _EVENT_LIMIT:
            raise RanTooLong("程序运行太久，已被停止")
        return tracer

    return tracer


def _forget_user_modules():
    os.chdir(_HOME)
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if isinstance(f, str) and f.startswith(_HOME + os.sep):
            del sys.modules[name]
    importlib.invalidate_caches()
    if _HOME not in sys.path:
        sys.path.insert(0, _HOME)


def _describe(e, res):
    res["ok"] = False
    res["error_type"] = type(e).__name__
    if isinstance(e, SyntaxError):
        res["error"] = e.msg
        res["line"] = e.lineno if e.filename == "<cell>" else None
        res["tb"] = "".join(traceback.format_exception_only(type(e), e))
        return
    res["error"] = str(e)
    frames = [
        f
        for f in traceback.extract_tb(e.__traceback__)
        if f.filename in ("<cell>", "<check>") or f.filename.startswith(_HOME + os.sep)
    ]
    for f in frames:
        if f.filename == "<cell>":
            res["line"] = f.lineno
    lines = ["Traceback (most recent call last):\n"]
    lines += traceback.StackSummary.from_list(frames).format()
    lines += traceback.format_exception_only(type(e), e)
    res["tb"] = "".join(lines)


def _exec_guarded(code, filename, ns, res):
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    try:
        compiled = compile(code, filename, "exec")
        sys.settrace(_make_tracer())
        try:
            exec(compiled, ns)
        finally:
            sys.settrace(None)
    except SystemExit as e:
        res["exit"] = "" if e.code in (None, 0) else str(e.code)
    except BaseException as e:
        _describe(e, res)


def _blank():
    return {"ok": True, "error_type": None, "error": None, "line": None, "tb": None, "exit": None}


def run_cell(code, check=None):
    global _tee
    _forget_user_modules()
    tee = _tee = _Tee()
    ns = {"__name__": "__main__", "__builtins__": builtins}
    res = _blank()
    res["check"] = None
    res["check_msg"] = None
    saved = builtins.input, sys.stdout, sys.stderr
    builtins.input = _input
    sys.stdout = sys.stderr = tee
    try:
        _exec_guarded(code, "<cell>", ns, res)
        if check is not None and res["ok"]:
            ns["__output__"] = "".join(tee.program)
            ns["__source__"] = code
            sink = _Tee()
            sys.stdout = sys.stderr = sink
            chk = _blank()
            _exec_guarded(check, "<check>", ns, chk)
            if chk["ok"]:
                res["check"] = "pass"
            else:
                res["check"] = "fail"
                if chk["error_type"] == "AssertionError":
                    res["check_msg"] = chk["error"] or "有一项检查没通过。"
                else:
                    res["check_msg"] = chk["error_type"] + ": " + (chk["error"] or "")
    finally:
        builtins.input, sys.stdout, sys.stderr = saved
        _tee = None
    res["output"] = "".join(tee.transcript)
    return res
`; // END RUNNER

  let pyPromise = null;
  let inputProvider = null;

  function setStatus(state, text) {
    document.querySelectorAll(".py-status").forEach((s) => {
      s.dataset.state = state;
      const t = s.querySelector(".txt");
      if (t) t.textContent = text;
      s.title = text;
    });
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("Could not load " + src));
      document.head.append(s);
    });
  }
  function getPython() {
    if (!pyPromise) {
      setStatus("loading", "Python 启动中…");
      pyPromise = (async () => {
        if (!window.loadPyodide) await loadScript(PYODIDE_URL + "pyodide.js");
        const py = await window.loadPyodide({ indexURL: PYODIDE_URL });
        py.registerJsModule("teardown_io", {
          next_input: (promptText) => {
            const v = inputProvider ? inputProvider(String(promptText)) : undefined;
            return v === null || v === undefined ? undefined : String(v);
          },
        });
        py.runPython(RUNNER);
        setStatus("ready", "Python 已就绪");
        return py;
      })().catch((err) => {
        pyPromise = null;
        setStatus("failed", "Python 没加载成功");
        throw err;
      });
    }
    return pyPromise;
  }

  // ---------------------------------------------------------------- editor
  function insertText(ta, text) {
    ta.focus();
    let ok = false;
    try {
      ok = document.execCommand("insertText", false, text); // keeps undo history
    } catch (e) {
      ok = false;
    }
    if (!ok) {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
      ta.dispatchEvent(new Event("input"));
    }
  }

  function makeEditor(code, onRun) {
    const root = el("div", "editor");
    const gutter = el("div", "gutter");
    gutter.setAttribute("aria-hidden", "true");
    const area = el("div", "code-area");
    const pre = el("pre");
    pre.setAttribute("aria-hidden", "true");
    const ta = el("textarea");
    ta.spellcheck = false;
    ta.setAttribute("wrap", "off");
    ta.setAttribute("autocapitalize", "off");
    ta.setAttribute("autocomplete", "off");
    ta.setAttribute("autocorrect", "off");
    ta.setAttribute("aria-label", "Python 代码（可以编辑）");
    ta.value = code;
    area.append(pre, ta);
    root.append(gutter, area);

    let escPressed = false;
    function sync() {
      pre.innerHTML = highlight(ta.value) + "\n ";
      const n = ta.value.split("\n").length;
      gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
      ta.style.height = "auto";
      const extra = ta.scrollWidth > ta.clientWidth + 1 ? 14 : 0;
      ta.style.height = ta.scrollHeight + extra + "px";
      pre.scrollLeft = ta.scrollLeft;
    }
    ta.addEventListener("input", sync);
    ta.addEventListener("scroll", () => {
      pre.scrollLeft = ta.scrollLeft;
    });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        escPressed = true; // Esc then Tab leaves the editor (keyboard users)
        return;
      }
      if ((e.key === "Enter" && (e.ctrlKey || e.metaKey)) || (e.key === "Enter" && e.shiftKey)) {
        e.preventDefault();
        onRun();
        return;
      }
      const v = ta.value;
      const s = ta.selectionStart;
      const end = ta.selectionEnd;
      const lineStart = v.lastIndexOf("\n", s - 1) + 1;
      if (e.key === "Tab") {
        if (escPressed) {
          escPressed = false;
          return;
        }
        e.preventDefault();
        const multi = v.slice(s, end).includes("\n");
        if (!multi && !e.shiftKey) {
          insertText(ta, "    ");
          return;
        }
        // indent / dedent every selected line
        const blockEnd = end > s && v[end - 1] === "\n" ? end - 1 : end;
        const block = v.slice(lineStart, blockEnd);
        const changed = block
          .split("\n")
          .map((l) => (e.shiftKey ? l.replace(/^ {1,4}/, "") : "    " + l))
          .join("\n");
        ta.setSelectionRange(lineStart, blockEnd);
        insertText(ta, changed);
        ta.setSelectionRange(lineStart, lineStart + changed.length);
        return;
      }
      escPressed = false;
      if (e.key === "Enter" && !e.altKey) {
        const line = v.slice(lineStart, s);
        let indent = line.match(/^ */)[0];
        if (/:\s*(#.*)?$/.test(line)) indent += "    ";
        e.preventDefault();
        insertText(ta, "\n" + indent);
        return;
      }
      if (e.key === "Backspace" && s === end && s > lineStart) {
        const before = v.slice(lineStart, s);
        if (/^ +$/.test(before)) {
          e.preventDefault();
          const remove = before.length % 4 || 4;
          ta.setSelectionRange(s - remove, s);
          insertText(ta, "");
          return;
        }
      }
    });
    sync();
    return {
      root,
      textarea: ta,
      sync,
      get value() {
        return ta.value;
      },
      set value(v) {
        ta.value = v;
        sync();
      },
    };
  }

  // ---------------------------------------------------------------- cells
  const allCells = [];

  function buildCell(cell) {
    const code = codeOf(cell, 'script[type="text/x-python"]:not(.check):not(.solution)') || "";
    const check = codeOf(cell, 'script[type="text/x-python"].check');
    const solution = codeOf(cell, 'script[type="text/x-python"].solution');
    cell.querySelectorAll('script[type="text/x-python"]').forEach((s) => s.remove());

    const state = { cell, original: code, check, solution };
    const bar = el("div", "cell-bar");
    bar.append(el("span", "label", cell.dataset.label || (check ? "你的代码" : "试一试 · 可以改")));

    const runBtn = el("button", "btn run");
    runBtn.type = "button";
    runBtn.innerHTML = `运行 <span class="k">${isMac ? "⌘↵" : "Ctrl+↵"}</span>`;
    runBtn.addEventListener("click", () => run(state, "run"));
    bar.append(runBtn);

    if (check) {
      const checkBtn = el("button", "btn check", "检查我的代码");
      checkBtn.type = "button";
      checkBtn.addEventListener("click", () => run(state, "check"));
      bar.append(checkBtn);
      state.checkBtn = checkBtn;
    }
    const resetBtn = el("button", "btn ghost", "重置");
    resetBtn.type = "button";
    resetBtn.title = "恢复成原来的代码";
    resetBtn.addEventListener("click", () => {
      state.editor.value = state.original;
      state.term.textContent = "";
      state.extra.replaceChildren();
    });
    bar.append(resetBtn);
    if (solution) {
      const solBtn = el("button", "btn ghost", "参考答案");
      solBtn.type = "button";
      solBtn.addEventListener("click", () => toggleSolution(state, solBtn));
      bar.append(solBtn);
    }
    state.runBtn = runBtn;

    state.editor = makeEditor(code, () => run(state, check ? "check" : "run"));
    cell.append(bar, state.editor.root);

    if (cell.dataset.inputs !== undefined) {
      const box = el("div", "inputs");
      const id = "in-" + Math.random().toString(36).slice(2, 9);
      const label = el("label", null, "给 input() 的键盘输入 · 每行一条");
      label.htmlFor = id;
      const ta = el("textarea");
      ta.id = id;
      ta.rows = Math.min(5, Math.max(2, cell.dataset.inputs.split("|").length));
      ta.value = cell.dataset.inputs.split("|").join("\n");
      ta.spellcheck = false;
      box.append(label, ta, el("span", "hint", "这些行用完之后，input() 会收到「输入结束」（相当于按 Ctrl+D）。"));
      cell.append(box);
      state.inputs = ta;
    }

    state.term = el("div", "terminal");
    state.term.setAttribute("role", "log");
    state.term.setAttribute("aria-live", "polite");
    state.extra = el("div");
    cell.append(state.term, state.extra);

    cell.addEventListener("focusin", warmUp, { once: true });
    cell.addEventListener("pointerdown", warmUp, { once: true });
    allCells.push(state);
  }

  function warmUp() {
    getPython().catch(() => {});
  }

  function toggleSolution(state, btn) {
    if (state.solBox) {
      state.solBox.remove();
      state.solBox = null;
      btn.textContent = "参考答案";
      return;
    }
    const box = el("div", "solution-box");
    box.append(el("span", "label", "一种参考写法"));
    box.append(renderCodeBlock(state.solution));
    const use = el("button", "btn", "复制到我的编辑器");
    use.type = "button";
    use.addEventListener("click", () => {
      state.editor.value = state.solution;
    });
    const row = el("div", "finish-row");
    row.append(use, el("span", "zh", "先自己试一试，再来对照！"));
    box.append(row);
    state.cell.append(box);
    state.solBox = box;
    btn.textContent = "收起答案";
  }

  async function run(state, mode) {
    const buttons = state.cell.querySelectorAll(".cell-bar .btn");
    buttons.forEach((b) => (b.disabled = true));
    state.extra.replaceChildren();
    state.term.innerHTML = '<span class="placeholder">运行中…</span>';
    let py;
    try {
      if (!pyPromise) state.term.innerHTML = '<span class="placeholder">正在启动 Python（第一次运行要等几秒）…</span>';
      py = await getPython();
    } catch (err) {
      state.term.textContent = "";
      const box = el("div", "err-box");
      box.append(el("div", "err-title", "Python 没能启动"));
      const p = el("p");
      p.innerHTML =
        "浏览器版 Python 第一次使用时要从网上（<code>cdn.jsdelivr.net</code>）下载。检查一下网络，然后再点一次「运行」。";
      box.append(p);
      state.extra.append(box);
      buttons.forEach((b) => (b.disabled = false));
      return;
    }

    if (state.inputs) {
      const lines = state.inputs.value === "" ? [] : state.inputs.value.split("\n");
      inputProvider = () => (lines.length ? lines.shift() : undefined);
    } else {
      inputProvider = (p) => {
        const v = window.prompt(stripAnsi(p) || "程序在等你输入（点「取消」= 输入结束）：", "");
        return v === null ? undefined : v;
      };
    }
    await new Promise((r) => setTimeout(r, 30)); // let "Running…" paint before Python blocks the page

    let res;
    const fn = py.globals.get("run_cell");
    try {
      const proxy = fn(state.editor.value, mode === "check" ? state.check : null);
      res = proxy.toJs({ dict_converter: Object.fromEntries });
      proxy.destroy();
    } catch (err) {
      res = { ok: false, error_type: "InternalError", error: String(err), output: "" };
    } finally {
      fn.destroy();
      inputProvider = null;
    }
    showResult(state, res, mode);
    buttons.forEach((b) => (b.disabled = false));
  }

  function showResult(state, res, mode) {
    let html = ansiToHtml(res.output || "");
    if (res.exit !== null && res.exit !== undefined) {
      html += `<span class="sys">${html && !html.endsWith("\n") ? "\n" : ""}[程序已退出${res.exit ? "：" + esc(res.exit) : ""}]</span>`;
    }
    if (!html && res.ok) html = '<span class="placeholder">（没有输出：程序运行了，但没有打印任何东西）</span>';
    state.term.innerHTML = html;

    if (!res.ok) {
      const box = el("div", "err-box");
      const where = res.line ? `第 ${res.line} 行 · ` : "";
      const name = res.error_type === "RanTooLong" ? "已停止" : res.error_type;
      box.append(el("div", "err-title", `${where}${name}: ${res.error || ""}`));
      const tip = el("p");
      tip.innerHTML = TIPS[res.error_type] || "读一读上面的消息：它说明了出了什么错、在哪一行。";
      box.append(tip);
      if (res.tb && res.error_type !== "RanTooLong") {
        const d = el("details");
        d.append(el("summary", null, "查看完整的 traceback（在终端里 Python 会打印这些）"));
        d.append(el("pre", null, res.tb));
        box.append(d);
      }
      state.extra.append(box);
    }
    if (mode === "check") {
      const r = el("div", "result");
      if (!res.ok) {
        r.classList.add("fail");
        r.innerHTML = "还差一点。<span>先修好上面的错误，再检查一次。</span>";
      } else if (res.check === "pass") {
        r.classList.add("pass");
        r.innerHTML = "✓ 通过！<span>所有检查都通过了，干得漂亮。</span>";
        const ch = state.cell.closest(".challenge");
        if (ch) {
          ch.classList.add("done");
          markItem(ch.id);
        }
      } else {
        r.classList.add("fail");
        r.innerHTML = `还差一点。<span>${esc(res.check_msg || "有一项检查没通过。")}</span>`;
      }
      state.extra.append(r);
    }
  }

  // ---------------------------------------------------------------- snippets & excerpts
  function buildSnippet(box) {
    const code = codeOf(box, 'script[type="text/x-python"]') || "";
    const pre = renderCodeBlock(code, {
      numbered: box.dataset.numbers !== undefined,
      hl: parseLineSet(box.dataset.highlight),
    });
    box.replaceWith(pre);
  }
  function buildExcerpt(fig) {
    const code = codeOf(fig, 'script[type="text/x-python"]') || "";
    fig.querySelectorAll('script[type="text/x-python"]').forEach((s) => s.remove());
    const start = Number(fig.dataset.start || 1);
    const end = start + code.split("\n").length - 1;
    const head = el("div", "excerpt-head");
    head.append(el("span", "tag", "拆自机器人"));
    head.append(el("span", "where", `${fig.dataset.file || ""} · 第 ${start}–${end} 行`));
    if (fig.dataset.translated !== undefined) head.append(el("span", "translated", "注释已翻译"));
    const pre = renderCodeBlock(code, { start, numbered: true, hl: parseLineSet(fig.dataset.highlight) });
    fig.prepend(head, pre);
  }

  // ---------------------------------------------------------------- quizzes
  function buildQuiz(quiz) {
    const answer = (quiz.dataset.answer || "").trim().toLowerCase();
    const explain = quiz.querySelector(".explain");
    if (explain) explain.hidden = true;
    const feedback = el("p", "feedback");
    feedback.setAttribute("aria-live", "polite");
    const items = [...quiz.querySelectorAll(".options > li")];
    items.forEach((li, i) => {
      const letter = String.fromCharCode(97 + i);
      const btn = el("button");
      btn.type = "button";
      btn.innerHTML = `<span class="letter">${letter.toUpperCase()}</span><span>${li.innerHTML}</span>`;
      const why = li.dataset.why;
      li.replaceChildren(btn);
      btn.addEventListener("click", () => {
        if (quiz.classList.contains("answered")) return;
        if (letter === answer) {
          btn.classList.add("right");
          quiz.classList.add("answered", "done");
          feedback.className = "feedback right";
          feedback.textContent = "答对了！";
          if (explain) explain.hidden = false;
          markItem(quiz.id);
        } else {
          btn.classList.add("wrong");
          btn.disabled = true;
          feedback.className = "feedback wrong";
          feedback.textContent = why ? `不太对。${why}` : "不太对，换个答案试试。";
        }
      });
    });
    const opts = quiz.querySelector(".options");
    if (opts) opts.after(feedback);
  }

  // ---------------------------------------------------------------- top bar & footer
  function root() {
    return document.body.dataset.root || "";
  }
  function lessonHref(l) {
    return root() + "lessons/" + l.file;
  }

  function buildTopbar() {
    const id = lessonId();
    const idx = LESSONS.findIndex((l) => l.id === id);
    const bar = el("header", "topbar");
    const inner = el("div", "topbar-inner");
    const logo = el("a", "logo");
    logo.href = root() + "index.html";
    logo.innerHTML = '拆机学 Python<span class="cursor" aria-hidden="true"></span>';
    inner.append(logo);
    if (idx >= 0) inner.append(el("span", "lesson-num", `第 ${id} 课 / ${LESSONS[LESSONS.length - 1].id}`));
    inner.append(el("span", "spacer"));
    if (document.querySelector(".cell")) {
      const st = el("span", "py-status");
      st.dataset.state = "idle";
      st.append(el("span", "txt", "Python 待命"));
      st.title = "第一次运行代码时 Python 才会启动";
      inner.append(st);
    }
    if (idx >= 0) {
      const nav = el("nav");
      nav.setAttribute("aria-label", "课程导航");
      if (idx > 0) {
        const a = el("a", null, "← 上一课");
        a.href = LESSONS[idx - 1].file;
        a.title = LESSONS[idx - 1].title;
        nav.append(a);
      }
      if (idx < LESSONS.length - 1) {
        const a = el("a", null, "下一课 →");
        a.href = LESSONS[idx + 1].file;
        a.title = LESSONS[idx + 1].title;
        nav.append(a);
      }
      inner.append(nav);
    }
    const theme = el("button", "chip-btn");
    theme.type = "button";
    const label = () => (theme.textContent = effectiveTheme() === "dark" ? "☀ 浅色" : "☾ 深色");
    label();
    theme.addEventListener("click", () => {
      const next = effectiveTheme() === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (e) {
        /* ignore */
      }
      label();
    });
    inner.append(theme);
    bar.append(inner);
    document.body.prepend(bar);
  }

  let finishBox = null;
  function updateFinish() {
    if (!finishBox) return;
    const id = lessonId();
    const items = [...document.querySelectorAll(".quiz[id], .challenge[id]")];
    const p = loadProgress();
    const r = record(p, id);
    r.total = items.length;
    saveProgress(p);
    const done = items.filter((it) => r.done[it.id]).length;
    finishBox.querySelector(".progress-line").textContent = items.length
      ? `本页：小测验和挑战已完成 ${done} / ${items.length}。`
      : "本页没有小测验。";
    const btn = finishBox.querySelector(".complete-btn");
    btn.textContent = r.complete ? "✓ 本课已学完（点击撤销）" : "标记本课已学完";
    btn.classList.toggle("check", !!r.complete);
  }
  function buildFinish(main) {
    const id = lessonId();
    const idx = LESSONS.findIndex((l) => l.id === id);
    if (idx < 0) return;
    const box = el("section", "finish");
    box.append(el("h2", null, "这一课学完了吗？"));
    box.firstChild.style.marginTop = "0";
    box.append(el("p", "progress-line"));
    const row = el("div", "finish-row");
    const btn = el("button", "btn complete-btn");
    btn.type = "button";
    btn.addEventListener("click", () => {
      const p = loadProgress();
      const r = record(p, id);
      r.complete = !r.complete;
      saveProgress(p);
      updateFinish();
    });
    const home = el("a", "btn ghost", "课程地图");
    home.href = root() + "index.html";
    row.append(btn, home);
    box.append(row);
    const nav = el("nav", "lesson-nav");
    nav.setAttribute("aria-label", "上一课和下一课");
    if (idx > 0) {
      const a = el("a", "prev");
      a.href = LESSONS[idx - 1].file;
      a.append(el("small", null, `← 第 ${LESSONS[idx - 1].id} 课`), el("strong", null, LESSONS[idx - 1].title));
      nav.append(a);
    }
    if (idx < LESSONS.length - 1) {
      const a = el("a", "next");
      a.href = LESSONS[idx + 1].file;
      a.append(el("small", null, `第 ${LESSONS[idx + 1].id} 课 →`), el("strong", null, LESSONS[idx + 1].title));
      nav.append(a);
    }
    box.append(nav);
    main.append(box);
    finishBox = box;
    updateFinish();
  }

  // ---------------------------------------------------------------- step mode
  // 每个顶层 <section>（以 <h2> 开头）是一步；默认一屏只显示一步，可切换「全部展开」。
  const MODE_KEY = "agent-teardown-mode";
  function buildSteps(main) {
    const id = lessonId();
    const steps = [...main.children].filter((s) => s.tagName === "SECTION" && !s.classList.contains("finish"));
    if (!id || steps.length < 3) return;
    const finish = main.querySelector(":scope > section.finish");
    const hero = document.querySelector(".hero");
    const titles = steps.map((s, i) => {
      const h = s.querySelector("h2");
      return h ? h.textContent.trim() : `第 ${i + 1} 步`;
    });

    let mode = "steps";
    try {
      if (localStorage.getItem(MODE_KEY) === "all") mode = "all";
    } catch (e) {
      /* ignore */
    }
    const saved = Number(record(loadProgress(), id).step);
    let current = Number.isInteger(saved) && saved >= 0 && saved < steps.length ? saved : 0;

    const bar = el("nav", "stepper");
    bar.setAttribute("aria-label", "本课步骤");
    const head = el("div", "stepper-head");
    const where = el("span", "where");
    const toggle = el("button", "chip-btn");
    toggle.type = "button";
    head.append(where, toggle);
    const track = el("div", "track");
    const fill = el("span");
    track.append(fill);
    const list = el("ol");
    const chips = titles.map((t, i) => {
      const li = el("li");
      const b = el("button");
      b.type = "button";
      b.append(el("span", "n", String(i + 1)), el("span", "t", t));
      b.addEventListener("click", () => go(i));
      li.append(b);
      list.append(li);
      return b;
    });
    bar.append(head, track, list);
    main.prepend(bar);

    steps.forEach((s, i) => {
      s.classList.add("step");
      const nav = el("div", "step-nav");
      if (i > 0) {
        const b = el("button", "btn ghost", "← 上一步");
        b.type = "button";
        b.addEventListener("click", () => go(i - 1));
        nav.append(b);
      }
      if (i < steps.length - 1) {
        const b = el("button", "btn run next", `下一步：${titles[i + 1]} →`);
        b.type = "button";
        b.addEventListener("click", () => go(i + 1));
        nav.append(b);
      }
      s.append(nav);
    });

    function render() {
      const stepMode = mode === "steps";
      document.body.classList.toggle("all-mode", !stepMode);
      steps.forEach((s, i) => (s.hidden = stepMode && i !== current));
      if (finish) finish.hidden = stepMode && current !== steps.length - 1;
      if (hero) hero.hidden = stepMode && current > 0;
      chips.forEach((c, i) => {
        c.setAttribute("aria-current", stepMode && i === current ? "step" : "false");
        c.classList.toggle("seen", stepMode && i < current);
      });
      track.hidden = !stepMode;
      fill.style.width = `${((current + 1) / steps.length) * 100}%`;
      where.textContent = stepMode ? `第 ${current + 1} 步 / 共 ${steps.length} 步` : `共 ${steps.length} 步 · 已全部展开`;
      toggle.textContent = stepMode ? "全部展开" : "分步学习";
      allCells.forEach((c) => {
        if (!c.cell.closest("[hidden]")) c.editor.sync();
      });
    }
    function go(i) {
      if (mode === "steps") {
        current = i;
        const p = loadProgress();
        record(p, id).step = i;
        saveProgress(p);
        render();
        window.scrollTo({ top: Math.max(0, bar.getBoundingClientRect().top + window.scrollY - 70) });
      } else {
        window.scrollTo({ top: Math.max(0, steps[i].getBoundingClientRect().top + window.scrollY - 70) });
      }
    }
    toggle.addEventListener("click", () => {
      mode = mode === "steps" ? "all" : "steps";
      try {
        localStorage.setItem(MODE_KEY, mode);
      } catch (e) {
        /* ignore */
      }
      render();
    });
    render();
  }

  function restoreDone() {
    const id = lessonId();
    if (!id) return;
    const r = loadProgress()[id];
    if (!r || !r.done) return;
    document.querySelectorAll(".challenge[id]").forEach((c) => {
      if (r.done[c.id]) c.classList.add("done");
    });
    document.querySelectorAll(".quiz[id]").forEach((q) => {
      if (r.done[q.id]) q.classList.add("done");
    });
  }

  // ---------------------------------------------------------------- boot
  document.addEventListener("DOMContentLoaded", () => {
    buildTopbar();
    document.querySelectorAll(".snippet").forEach(buildSnippet);
    document.querySelectorAll("figure.excerpt").forEach(buildExcerpt);
    document.querySelectorAll(".cell").forEach(buildCell);
    document.querySelectorAll(".quiz").forEach(buildQuiz);
    restoreDone();
    const main = document.querySelector("main.lesson");
    if (main) {
      buildFinish(main);
      buildSteps(main);
    }
    // 折叠块（details）里的编辑器在关闭时量不到高度，打开时重新量
    document.addEventListener(
      "toggle",
      (e) => {
        if (e.target.open) allCells.forEach((c) => e.target.contains(c.cell) && c.editor.sync());
      },
      true
    );
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => allCells.forEach((c) => c.editor.sync()));
    }
    window.addEventListener("resize", () => allCells.forEach((c) => c.editor.sync()));
  });

  window.AgentTeardown = {
    LESSONS,
    UNITS,
    loadProgress,
    saveProgress,
    highlight,
    renderCodeBlock,
    getPython,
    effectiveTheme,
  };
})();
