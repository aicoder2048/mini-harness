# 拆机学 Python（Agent Teardown）

给中学生的 Python 入门课（中文），用本仓库的 mini-harness 当教具：
15 节课，从变量讲到类和测试，每个概念都落在 `src/` 里的真实代码上。

## 怎么用

直接用浏览器打开 `index.html`（双击即可，不需要服务器）。

- 课程首页：`index.html`（课程地图 + 学习进度）
- 教师指南：`teacher-guide.html`（中文：节奏、每课讨论题、真机演示命令）
- 课文：`lessons/00-welcome.html` … `lessons/14-testing.html`

每课分成 4–7 步，默认一屏只显示一步（顶部步骤条 +「下一步」按钮），点「全部展开」可以一页看完。
页面里的代码格运行的是真正的 Python 3.12（Pyodide，第一次运行时从 `cdn.jsdelivr.net` 下载约 15 MB，
之后浏览器有缓存）。死循环几秒后自动停止；`input()` 从代码格下方的输入框取值；报错会显示行号、
中文提示和完整 traceback。

## 目录

```
index.html            课程首页
teacher-guide.html    教师指南
lessons/              15 节课
assets/course.css     共享样式（颜色取自 agent 终端的 ANSI 配色）
assets/course.js      编辑器、Pyodide 运行器、选择题、自动判分、进度
tools/check_lessons.py  课程校验脚本
```

## 校验

```bash
uv run rh-learn-python/tools/check_lessons.py        # 全部课
uv run rh-learn-python/tools/check_lessons.py 05 -v  # 只看第 05 课，并打印每个代码格的输出
```

它用 `course.js` 里同一套运行器在本机执行每个代码格，检查：参考答案能通过判分、初始代码不能通过、
「Found in the robot」摘录与 `src/` 原文逐行一致。改了 `src/` 之后跑一次，就知道哪些摘录的行号过期了。
