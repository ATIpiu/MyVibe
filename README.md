# MyVibe — Python AI 编程助手

> **完成比完美重要。** 目前自用体验已勉强可用，欢迎 fork 按自己的习惯魔改。

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

- **流式对话** — LLM 响应实时输出，低延迟
- **工具调用** — 文件读写编辑、Shell 执行、Git 操作、代码搜索
- **权限管控** — 三级权限：自动执行 / 需确认 / 永久拒绝
- **会话持久化** — JSONL 格式保存，支持 `--continue` 和 `--resume` 恢复历史会话
- **上下文压缩** — Token 占比超过阈值时自动 LLM 摘要压缩
- **轮次回退** — 基于 Git 提交的对话轮次回退（`/revert`）
- **项目记忆** — AST 扫描代码库，构建函数级记忆索引，每轮自动注入相关上下文
- **OpenAI 兼容** — 对接任意兼容 OpenAI Chat Completions 协议的服务（OpenAI、DeepSeek、OpenRouter 等）
- **Windows 兼容** — 自动处理 GBK / UTF-8 编码问题

---

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

或：

```bash
pip install -r requirments.txt
```

### 2. 配置

```bash
cp config/config.example.yaml config/config.yaml
```

编辑 `config/config.yaml`，填入你的 API Key 和服务地址：

```yaml
llm:
  provider: openai
  model: gpt-4o                          # 按所用服务填写模型名
  api_key: "YOUR_API_KEY_HERE"
  base_url: "https://api.openai.com/v1"  # 任意兼容 OpenAI 协议的地址
```

也可以通过环境变量设置：

```bash
export OPENAI_API_KEY="your_key"
```

> 接 DeepSeek 示例：`base_url: "https://api.deepseek.com/v1"`，`model: deepseek-chat`
>
> 接 OpenRouter 示例：`base_url: "https://openrouter.ai/api/v1"`，模型按 OpenRouter 的 model ID 填

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

# 指定模型
myvibe --model deepseek-chat

# 指定工作目录
myvibe --cwd /path/to/project
```

---

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/init` | 扫描项目，用 LLM 生成 `MyVibe.md` 项目记忆文件 |
| `/clear` | 清除当前对话历史 |
| `/compact` | LLM 摘要压缩对话历史（节省 Token） |
| `/cost` | 显示当前会话 Token 用量和费用统计 |
| `/sessions` | 列出所有历史会话 |
| `/history` | 查看当前会话的对话轮次 Git 提交历史 |
| `/revert` | 交互式选择回退到某一轮次（同时回退文件和对话上下文） |
| `/plan` | 切换计划模式（只输出计划，不执行工具） |
| `/help` | 显示帮助 |
| `/exit` | 退出程序 |

快捷键：`Ctrl+P` 切换计划模式，`Ctrl+C` 退出。

---

## 项目结构

```
MyVibe/
├── src/
│   ├── main.py                 # CLI 入口，会话管理，斜杠命令
│   ├── agent/
│   │   ├── coding_agent.py     # 核心 Agentic 循环 + 权限管理
│   │   ├── plan_agent.py       # 计划模式
│   │   ├── sub_agent.py        # 子 Agent（用于隔离调用）
│   │   ├── project_init.py     # /init 项目记忆生成
│   │   └── state.py            # 运行时状态 + 会话管理
│   ├── llm/
│   │   ├── base_client.py      # LLM 客户端抽象基类
│   │   ├── openai_client.py    # OpenAI 兼容实现
│   │   ├── client.py           # 工厂函数
│   │   └── prompts.py          # 系统提示词构建
│   ├── tools/
│   │   ├── file.py             # 文件读写编辑搜索
│   │   ├── shell.py            # Shell 执行（注入检测）
│   │   ├── git.py              # Git 状态/Diff/提交/回退
│   │   ├── context_tools.py    # 项目上下文工具
│   │   ├── memory_tools.py     # 记忆系统工具
│   │   ├── agent_tools.py      # Agent 调用工具
│   │   └── ask_user_tool.py    # 主动询问用户
│   ├── memory/
│   │   ├── memory_manager.py   # 记忆系统入口
│   │   ├── ast_analyzer.py     # AST 函数扫描
│   │   └── storage.py          # 记忆存储（JSON）
│   ├── context/
│   │   ├── context_manager.py  # 文件摘要与上下文管理
│   │   └── parsers/            # Python / 通用代码解析器
│   ├── skills/                 # 可扩展技能系统
│   ├── tasks/                  # 任务管理
│   └── ui/                     # 键盘监听、折叠输出等 UI 组件
├── config/
│   ├── config.example.yaml    # 配置模板（提交到仓库）
│   └── config.yaml            # 实际配置（含 Key，不提交）
└── DOCS/                      # 设计文档与 TODO
```

---

## 配置说明

| 配置项 | 说明 |
|--------|------|
| `llm.provider` | LLM 提供商，目前统一为 `openai`（兼容协议） |
| `llm.model` | 模型名称 |
| `llm.api_key` | API Key（推荐用环境变量） |
| `llm.base_url` | API 地址，可替换为任意 OpenAI 兼容服务 |
| `agent.max_tool_workers` | 并行工具 Worker 数量 |
| `agent.context_compress_threshold` | 触发上下文压缩的 Token 占比阈值（0~1） |
| `agent.model_routing` | 是否按任务类型自动切换模型（默认关闭） |
| `permissions.*` | 工具三级权限配置 |
| `shell.timeout_ms` | Shell 命令超时（毫秒） |

---

## 路线图

- [ ] 真正的并行工具执行（当前串行）
- [ ] 更智能的上下文压缩策略
- [ ] 更好的 Token 成本控制
- [ ] 更快的响应速度
- [ ] 更多模型路由策略

欢迎提 Issue 和 PR，一起把这个工具做得更好用。

---

## License

MIT
