# Persona（多角色 Agent）教学设计备忘

> **状态：已实现（2026-10-16）。** 记录讨论结论：要做什么、为什么这样做、角色为什么选西游记。实现与设计不同的地方见第七节。
> 目的：让学生看到 **同一个模型 + 不同的 harness = 不同的 agent**。

## 一、已定的决定

| 问题 | 决定 | 理由 |
|---|---|---|
| Persona 文件格式 | Markdown + frontmatter，放 `.agents/personas/<id>.md` | 和 `SKILL.md` 同一种写法，复用 `skills.parse_frontmatter`，不加 PyYAML；提示词是大段文字，放正文最直观，孩子不用管 YAML 缩进 |
| frontmatter 字段 | `name`（拼音 id）、`title`（中文展示名）、`description`、`tools`（逗号分隔一行）、`emoji` | 现有解析器只认顶层单行键值，所以 `tools` 不能写列表 |
| 提示词语言 | persona 正文写**中文** | 孩子要读、要改。harness 内置段落（`# Agency` 等）和工具 description 仍是英文：「发给模型的文字用英文」约定针对的是工程师维护的内置文字；persona 正文是用户内容，和 `AGENTS.md`、`SKILL.md` 同类。需要在 AGENTS.md 补一句，并加一个 live test 确认中文 persona 下工具调用正常 |
| 对话 conversation | 每个 persona 一份，切回来接着聊 | 1) DeepSeek 按前缀缓存，各管各的前缀才稳定；2) 对话混在一起模型会分不清自己是谁 |
| 记忆 | 每个 persona 一个目录 `Memory/<id>/` | 演示「记忆隔离」 |
| 审批状态 | 每个 persona 一个 `ConsoleApprover` | 「a=本会话都允许」不带到别的角色 |
| 共享的东西 | 只有工作目录里的文件（`PLAN.md` 等） | agent 之间不共享对话，靠文件交接；孩子当项目经理负责传话 |
| 检查员能否写文件 | **纯只读**，不做「软围墙」 | 围墙最干净：禁止某个动作靠不给工具，不靠提示词劝说 |
| Persona 数量 | 一次做 3 个 | |

## 二、切换时什么变、什么不变

| 组件 | 切换后 |
|---|---|
| 模型 | **不变**（同一个 DeepSeek） |
| system prompt | 变：`PromptContext` 加 `persona` 字段，替换写死的第一句 "You are a coding agent..." |
| 可用工具 | 变：按 `tools:` 过滤 `ALL_TOOLS` |
| `# Guardrails` / `# Verification` 段 | 自动跟着变（本来就取决于有没有 `edit_file` / `run_bash`） |
| 对话 / 记忆 / 审批状态 | 各自独立 |
| 工作目录里的文件 | 共享 |

## 三、斜杠命令

斜杠命令要能带参数（现在 `slash_command()` 忽略命令后面的参数）。

| 命令 | 作用 |
|---|---|
| `/persona` | 列出所有 persona，标出当前的 |
| `/persona <id>` | 切换，并打印变化摘要（工具数、提示词字符数、对话条数、记忆目录） |
| `/persona reload` | 重新读 `.md`，重建当前 persona 的提示词和工具，**保留它的对话**（提示词变了，缓存会重算一次） |
| `/prompt` | 显示当前完整 system prompt，附字符数 |
| `/tools` | 列出当前工具：名字、中文说明（`Tool.summary_zh`，**只显示，不发给模型**）、是否需要审批；`/tools <name>` 看英文 description 和 schema |
| `/status` | 一屏看 5 个组件：persona、工具数、对话条数、上次 input token、记忆目录和条数 |

切换时的对比提示是教学重点，例如：

```
🔁 悟空 → 八戒
  工具    4 → 2    （− edit_file  − run_bash）
  提示词  2,410 → 1,180 字符    （− Guardrails  − Verification）
  对话    八戒自己的，0 条（悟空的 14 条已保留）
  记忆    Memory/bajie/（0 条）
```

## 四、角色：西游记三人组

| | 展示名 | id | 工具 | 职责 |
|---|---|---|---|---|
| 策划 | 🧘 唐僧 | `tang` | read_file、list_files、edit_file | 向孩子提问，写出 `PLAN.md`（要做什么、怎样算做完） |
| 程序员 | 🐒 孙悟空 | `wukong` | 全部 4 个（run_bash 需审批） | 按 `PLAN.md` 写代码，运行验证 |
| 检查员 | 🐷 猪八戒 | `bajie` | read_file、list_files | 对照 `PLAN.md` 检查代码，给出清单，不能改 |

斜杠命令只认字母，所以 id 用拼音；展示名用中文。

### 为什么选西游记（而不是漫威、我的世界）

1. **角色和 harness 概念能对上**
   - 悟空本领最大，4 个工具全有。
   - 唐僧不会打架，只负责定目标、念「经」（`PLAN.md`），没有 `run_bash`。
   - 八戒眼尖嘴碎、只动嘴不动手，正好是只读检查员：「俺老猪只管看，不管改」。
   - **紧箍咒 = guardrail 和审批**：悟空本领再大，也要听规矩。这是最有教学价值的类比。
2. **孩子熟**：黑神话悟空、哪吒 2 之后，这批孩子对西游记热情很高。
3. **没有版权顾虑**：西游记是公版。课件会发到 here.now 公开，漫威、任天堂、Mojang 的角色名放进公开材料不稳妥。
4. **性格鲜明**：模型扮演这三个人物很在行，提示词不用写太长，孩子也容易看出「改提示词 = 改性格」。

**八戒的写法要点**：语气可以搞笑，但检查结论必须准确、条理清楚，不能为了好玩漏掉 bug。

备选（未采用）：

| | 策划 | 程序员 | 检查员 | 没选的原因 |
|---|---|---|---|---|
| 漫威 | 尼克·弗瑞 | 蜘蛛侠 | 奇异博士（「看过一千四百万种未来，只看不动」很贴检查员） | 版权；蜘蛛侠和「程序员」对不上 |
| 我的世界 | 村民 | 史蒂夫 | 苦力怕 | 版权；三个角色的职责对应牵强 |

## 五、教学场景：三人小队做一个小程序

任务示例：「猜数字游戏」，用到变量、判断、while 循环（教材前几课）。

四个演示实验，每个对应一个 harness 概念：

1. **同一句话三个反应**：对三个角色都说「帮我把 game.py 修好」。唐僧追问需求，悟空改完再跑，八戒说「我没有改文件的工具，但我找到了 3 处问题」。再用 `/tools` 和 `/prompt` 解释原因。结论：**同一个模型，不同的 harness，就是不同的 agent**。
2. **记忆隔离**：对唐僧说「我喜欢太空游戏」，切到悟空问「我喜欢什么」，它不知道；切回唐僧，它还记得。
3. **围墙**：让八戒动手改代码，它做不到。最小权限：禁止靠的是不给工具，不是提示词劝说。
4. **接力**：唐僧写 `PLAN.md`，悟空实现，八戒检查，再回悟空修。agent 之间不共享对话，靠共享文件交接，孩子当项目经理传话。自动交接留给下一课（软件工厂）。

## 六、实现在哪里

- `src/personas.py`：`Persona`、`load_persona`、`discover_personas`（复用 `skills.parse_frontmatter`）。
- `src/prompt.py`：`PromptContext.persona`，有 persona 时替换第一句角色说明；没有工具时写 `Available tools: (none)`。
- `src/agent.py`：`Session`（每个角色的 system / tools / 对话 / 审批 / 记忆目录 / 上次输入 token / 文件路径）、
  `persona_session`、`switch_summary`、`/persona` `/persona reload` `/prompt` `/tools` `/status`、`--persona`。
- `src/tools.py`：`Tool.summary_zh`，只给 `/tools` 显示，不发给模型。
- `.agents/personas/`：`tang.md`、`wukong.md`、`bajie.md`。
- 测试：`tests/test_personas.py`，`tests/test_agent.py` 和 `tests/test_prompt.py` 的 persona 部分；
  `tests/test_live.py` 两个 live test（中文角色正常调用工具；reload 去掉工具后，含旧调用记录的对话仍被 DeepSeek 接受）。

## 七、实现与设计不同的地方

- `slash_command()` 没改，仍只返回命令名；参数用新函数 `command_argument()` 取。这样原有测试不用动。
- `default` 会话的记忆是整个记忆根目录，而读索引会进子目录，所以 `default` 能看到所有角色的笔记；三个角色只看到自己的 `Memory/<id>/`。演示「记忆隔离」时用三个角色。
- 指定了角色时，工具由 `tools:` 决定，`--step` 只对 `default` 起作用。
- `/persona reload` 除了对话，也保留审批状态和上次输入 token（同一个角色，只是换了配置）。
- 回复标签：有角色时显示「🐒 孙悟空 · DeepSeek」，提醒学生大脑还是同一个。
- 角色文件里提到同学一律写「用户」，不用「他」。

## 八、教程 `rh-learn-python/` 的刷新（已完成）

- 起点：校验脚本报 32 处摘录对不上（改之前是 0 处）。28 处只是行号整体移动，用脚本按原文重新定位 `data-start` / `data-highlight`；
  4 处摘录的代码本身变了，按现在的源码重写：第 07 课（回复标签）、第 10 课（`run_bash` 多了 `summary_zh`）、第 11 课（多了两行 import）、第 13 课（整个 `Agent.run`）。
- 课文里约 40 处手写的行号（「第 238 行」、`--lines` 横幅、第 13 课两张表）按 `git HEAD` 与现在源码的逐行对应关系改过，并逐一核对了指向的那一行。
- 第 13 课的行号表多了一行「400：每转一圈先拿出当前角色的对话记录」；第 12 课的 `Tool` 摘录改为完整的 9 行（含 `summary_zh`），「7 行的蓝图」改为「这张蓝图」。
- 结果：`uv run rh-learn-python/tools/check_lessons.py` 0 个问题。
- 「7 个文件」的说法（2026-10-16 已处理）：第 00、11、14 课、课程首页、教师指南和根目录 README 都改成 8 个文件、约 1,800 行；
  第 00 课零件图加了第三行 `personas.py`（「角色」箭头指向 `prompt.py`），零件表加了一行；第 11 课接线图改成 6 个零件，加上 `personas.py → skills.py`。
- 决定：`rh-learn-python` 不加 persona 的课（那门课讲 Python）。

## 九、Harness 系列教程的第一课

- 目录 `rh-learn-agent-harness/`（系列规划中）。第 01 课 `lessons/01-three-agents.html`「一个大脑，三个角色」：
  三个核心组件（系统提示词、可用工具、会话状态）+ 角色文件 + 第五节的四个实验。
- `assets/`、`tools/check_lessons.py` 复制自 `rh-learn-python`，只改了课程配置；校验：`uv run rh-learn-agent-harness/tools/check_lessons.py`。
- 课里的终端输出（`/persona`、`/tools`、`/prompt`、`/status`）是在练习文件夹里真实运行 harness 得到的，只缩短了路径；
  模型的回答每次不同，课里只写「大概会看到」，没有编造对话记录。
