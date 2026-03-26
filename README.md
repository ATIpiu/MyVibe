# MyVibe — Python AI 编程助手

> **完成比完美重要！！！** 目前自用体验已勉强可用，欢迎 fork 按自己的习惯魔改。

---

## 为什么做这个

市面上目前没有一个好用的、用纯 Python 写的 AI 编程 Agent 工具。

我是 Claude Code 的重度用户，用下来确实很爽，但有些功能我希望按自己的编程习惯去改——比如自定义工具、调整权限策略、接入不同的模型服务。然而 Claude Code 是闭源的，没法动。

于是干脆自己写一个。

**核心目标：**

- 用尽可能少的 Token，达到尽量接近 Claude Code 的效果
- 又便宜又快——降低成本、提升响应速度是长期方向
- 完全开源：所有人都可以根据自己的编程习惯自由修改，共同维护，让工具越来越好用

---

## 功能特性

### 对话与 Agent

- **流式输出** — LLM 响应实时流式打印，低延迟
- **Agentic 循环** — 自动处理多轮工具调用，直到 LLM 主动结束
- **计划模式** — `/plan` 进入只读模式，LLM 只输出计划，计划保存到 `.myvibe/plans/`
- **子 Agent** — `spawn_agent` 工具创建上下文隔离的子代理处理子任务，Token 互不污染
- **中断支持** — `Ctrl+C` 取消当前轮次，`Ctrl+O` 切换 Shell 输出的折叠/展开

### 工具系统

**文件访问逐级优先链**（节省 Token 的核心设计）：

工具的 description 里内置了明确的优先级引导，LLM 被要求按顺序逐层深入，而不是上来就读整个文件：

```
1. read_memory overview   → 了解项目整体结构（最省 Token）
2. glob_files             → 按文件名模式定位目标文件
3. grep_files             → 按内容正则搜索，定位到具体文件 + 行号
4. read_file offset+limit → 只读目标行范围，而非整个文件（最后手段）
```

各工具能力：

- **read_file** — 带行号分页读取，支持 `offset+limit` 精确读取目标行范围
- **write_file** — 完整写入/新建文件，自动创建父目录，写入后自动同步记忆索引
- **edit_file** — 精确字符串替换，`old_string` 必须唯一（否则报错并提示出现次数），支持 `replace_all`，编辑后返回修改位置上下文
- **glob_files** — Glob 模式文件匹配，按修改时间倒序返回
- **grep_files** — 正则内容搜索，优先调用 ripgrep，不存在时降级为 Python re；支持三种输出模式（文件列表 / 匹配内容+上下文 / 匹配计数）、大小写不敏感、结果数量限制
- **已读文件拦截** — 本轮已读过的文件再次被请求时，自动拦截并引导 LLM 改用 `read_memory function` 获取函数内容，避免重复消耗 Token
- **文件锁** — 写入/编辑按路径加锁，防止并发写冲突

- **Shell 执行** — 命令注入检测、危险命令分级、编码自适应（UTF-8 / GBK / latin-1）、超时控制、流式输出
- **Git 集成** — status、diff、commit；每轮对话自动打 `[turn-N]` Git 提交标记，支持按轮回退文件和上下文
- **提问工具** — `ask_user` 让 LLM 主动向用户提问，支持选项菜单或自由输入

### 权限管控

三级权限，行为通过 `config.yaml` 配置：

| 级别 | 行为 | 默认包含 |
|------|------|---------|
| `auto_allow` | 直接执行，无弹窗 | 文件读取、代码搜索、Git 查询、记忆读取 |
| `require_confirm` | 弹出确认框 | 文件写入、Shell 执行、Git 提交 |
| `deny` | 永久拒绝 | 可自定义危险命令 |

`/super` 命令可临时切换到免确认模式。

### 三层记忆系统

AST 解析代码库，构建函数级索引，LLM 按需分层查询：

| 层级 | 工具 | 内容 |
|------|------|------|
| 1 — 全局总览 | `read_memory overview` | 所有文件路径树 + 每个文件的用途一句话 |
| 2 — 文件详情 | `read_memory file` | 指定文件内所有函数名 + 用途 + 行号 |
| 3 — 函数源码 | `read_memory function` | 完整函数源码 + 调用/被调用关系 |

- 写文件/编辑文件成功后自动同步记忆索引
- `find_symbol` 快速定位符号所在函数，跳过前两层
- 存储在 `.vibecoding/memory/memory_tree.json`

### 会话管理

- **持久化** — 每轮后保存到 `.agent_sessions/<session_id>.jsonl`
- **恢复** — `--continue` 恢复上次会话，`--resume <session_id>` 恢复指定会话
- **上下文压缩** — Token 占比超过阈值（默认 92%）时，用 LLM 生成摘要替换历史消息
- **轮次回退** — `/revert` 交互选择回退到某轮次，文件状态和对话上下文同时回滚
- **会话自动命名** — 第一轮结束后异步用子 Agent 生成会话名（≤10字），不影响主流程

### Skills 系统

在 `.myvibe/skills/` 目录放 `.md` 文件，定义可复用的 prompt 模板：

```markdown
---
name: commit
description: 生成规范 Git commit message
---
请为以下改动生成 commit message：{args}
```

`/skills` 列出所有可用 Skill，`/skill commit` 触发对应模板。

### 后台任务

`/bg` 提交后台任务，`/tasks` 列表，`/task <id>` 查看详情，任务在独立线程运行。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

或直接安装：

```bash
pip install -r requirments.txt
```

### 2. 配置

```bash
cp config/config.example.yaml config/config.yaml
```

编辑 `config/config.yaml`，填入 API Key 和服务地址：

```yaml
llm:
  provider: openai
  model: gpt-4o                          # 按所用服务填写模型名
  api_key: "YOUR_API_KEY_HERE"
  base_url: "https://api.openai.com/v1"  # 任意兼容 OpenAI 协议的地址
```

也可以通过环境变量：

```bash
export OPENAI_API_KEY="your_key"
```

> 接 DeepSeek：`base_url: "https://api.deepseek.com/v1"`，`model: deepseek-chat`
>
> 接 OpenRouter：`base_url: "https://openrouter.ai/api/v1"`，model 按 OpenRouter model ID 填

### 3. 启动

```bash
# 交互模式
python -m src.main

# 安装后可直接运行
myvibe

# 一次性执行（headless 模式）
myvibe -p "用 Python 写一个快速排序并附上测试"

# 继续上次会话
myvibe --continue

# 恢复指定会话
myvibe --resume <session_id>

# 指定模型（覆盖 config.yaml）
myvibe --model deepseek-chat

# 指定工作目录
myvibe --cwd /path/to/project
```

---

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/init` | 扫描项目，生成 `AGENT.md` / `MyVibe.md` 项目记忆文件 |
| `/plan` | 进入计划模式（只读工具，输出计划并保存到 `.myvibe/plans/`） |
| `/compact` | 手动压缩对话历史（LLM 生成摘要，保留最近 10 条消息） |
| `/cost` | 显示本会话 Token 用量和估算费用 |
| `/sessions` | 列出最近 20 个历史会话 |
| `/history` | 显示当前会话的对话轮次 Git 提交历史 |
| `/revert` | 交互式回退到某个轮次（文件 + 上下文同时回滚） |
| `/context` | 显示当前上下文占用情况 |
| `/super` | 切换 super 模式（所有工具无需确认） |
| `/skills` | 列出所有可用 Skills |
| `/tasks` | 列出后台任务 |
| `/task <id>` | 查看指定任务详情 |
| `/bg` | 在后台提交任务 |
| `/clear` | 清空当前对话历史 |
| `/help` | 显示帮助 |
| `/exit` | 退出程序 |

**快捷键：**

| 按键 | 说明 |
|------|------|
| `Ctrl+C` | 取消当前轮次 |
| `Ctrl+O` | 切换 Shell 输出折叠/展开 |

---

## 项目结构

```
MyVibe/
├── src/
│   ├── main.py                     # CLI 入口，REPL 循环，斜杠命令
│   ├── agent/
│   │   ├── coding_agent.py         # 核心 Agentic 循环，权限管理，工具执行
│   │   ├── plan_agent.py           # 计划模式 Agent
│   │   ├── sub_agent.py            # 隔离子代理
│   │   ├── project_init.py         # /init 项目记忆生成
│   │   └── state.py                # 运行时状态，会话持久化/恢复，上下文压缩
│   ├── llm/
│   │   ├── base_client.py          # LLM 客户端抽象基类，历史收集
│   │   ├── openai_client.py        # OpenAI 兼容实现（流式 + 非流式）
│   │   ├── client.py               # 工厂函数，provider 注册表
│   │   └── prompts.py              # 系统提示词构建
│   ├── tools/
│   │   ├── file.py                 # 文件读写编辑搜索（含文件锁）
│   │   ├── shell.py                # Shell 执行（注入检测，编码自适应）
│   │   ├── git.py                  # Git 操作，轮次版本管理
│   │   ├── memory_tools.py         # 三层记忆读取，find_symbol
│   │   ├── ask_user_tool.py        # 向用户提问
│   │   ├── agent_tools.py          # spawn_agent schema
│   │   └── context_tools.py        # 项目上下文工具
│   ├── memory/
│   │   ├── memory_manager.py       # 记忆管理入口，单例，sync，render
│   │   ├── ast_analyzer.py         # Python AST 解析，函数/调用关系提取
│   │   ├── tree_storage.py         # 路径树存储
│   │   └── models.py               # ModuleData / FunctionData
│   ├── context/
│   │   ├── context_manager.py      # 文件摘要缓存，函数搜索
│   │   └── parsers/                # Python / 通用代码解析器
│   ├── skills/                     # Skill 加载器（.md 模板文件）
│   ├── tasks/                      # 后台任务管理器
│   ├── ui/
│   │   ├── key_listener.py         # Ctrl+O 后台监听（Windows msvcrt / Unix tty）
│   │   └── collapsible_output.py   # 可折叠流式输出面板
│   ├── completer/                  # prompt_toolkit 自动补全
│   └── logger/                     # 结构化日志（JSONL + Rich 控制台）
├── config/
│   ├── config.example.yaml         # 配置模板（提交到仓库）
│   └── config.yaml                 # 实际配置（含 Key，不提交）
└── DOCS/                           # 设计文档与 TODO
```

---

## 路线图

- [ ] 真正的并行工具执行（当前串行）
- [ ] 更智能的上下文压缩策略
- [ ] 更低的 Token 消耗
- [ ] 更快的响应速度

欢迎提 Issue 和 PR，一起把这个工具做得更好用。

---

## License

MIT
