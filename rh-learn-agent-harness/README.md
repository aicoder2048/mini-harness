# 拆机学 Harness（Harness Engineering）

给中学生的 Harness 工程课（中文），和《拆机学 Python》（`../rh-learn-python/`）用同一台机器人 mini-harness 当教具。
Python 课讲「代码怎么写」，这门课讲「harness 由哪些组件组成、怎么配置和自定义它们」。
重点在提示词层（Agentic Engineering 的原子单位），不讲实现代码的细节。

**状态：系列规划中，目前只有第 01 课。**

## 怎么用

直接用浏览器打开 `index.html`（双击即可，不需要服务器）。

| 课 | 文件 | 内容 |
|---|---|---|
| 01 | `lessons/01-three-agents.html` | 提示词层：模型每次读到什么、看懂和装配系统提示词、写好角色提示词、工具和对话也是提示词、动态加载与修剪、自定义角色；实践：西游记三人组的四个实验 |

页面里的代码格运行的是真正的 Python 3.12（Pyodide），和 Python 课一样。实验部分要在终端里运行 mini-harness，
用到 `.agents/personas/` 里的三个角色（设计见 `../docs/persona-design.md`）。

## 目录

```
index.html              课程首页
lessons/                课文
assets/course.css       复制自 rh-learn-python，未改
assets/course.js        复制自 rh-learn-python，只改了课程配置（STORE_KEY、UNITS、LESSONS、顶栏 logo）
tools/check_lessons.py  复制自 rh-learn-python，逻辑不变
tools/build_lesson01.py         生成第 01 课里「真的 harness 输出」：装配台数据和终端输出
tools/templates/01-three-agents.html  第 01 课的模板：要改课文，改这里
```

两套课各用一份 `assets/`：课号会重复（都有第 01 课），进度要分开存。改 `rh-learn-python/assets/` 里的共用行为时，
记得同步这里。

## 校验

```bash
uv run rh-learn-agent-harness/tools/check_lessons.py        # 全部课
uv run rh-learn-agent-harness/tools/check_lessons.py 01 -v  # 只看第 01 课，并打印每个代码格的输出
```

检查内容和 Python 课一样：代码格能跑、挑战题的参考答案能通过而初始代码不能、摘录与 `src/`、`.agents/personas/` 的原文逐行一致。
改了这些源文件之后跑一次。

第 01 课的装配台和终端输出是生成出来的，`check_lessons.py` 查不到它们过期，另用：

```bash
uv run rh-learn-agent-harness/tools/build_lesson01.py --check  # 页面和现在的 harness 不一致就报错
uv run rh-learn-agent-harness/tools/build_lesson01.py          # 重新生成 lessons/01-three-agents.html
```

`lessons/01-three-agents.html` 是生成物：要改课文，改 `tools/templates/01-three-agents.html`，再重新生成。
