# MyVibe — AI Coding Agent

一个受 Claude Code 启发、用纯 Python 构建的本地 AI 编程助手。通过 CLI 与 LLM 交互，支持文件读写、Shell 命令、Git 操作、代码搜索等工具，并具备会话持久化、上下文压缩和项目记忆系统。

---

## 功能特性

- **流式对话**：LLM 响应实时流式输出，低延迟
- **工具调用**：文件读写编辑、Shell 执行、Git 操作、LSP 代码分析
- **并行工具执行**：多工具同时运行（最多 4 个 Worker）/ TODO：假的，其实还还不支持
- **权限管控**：工具调用分 auto_allow / require_confirm / deny 三级
- **会话持久化**：JSONL 格式保存，支持 `--continue` 和 `--resume` 恢复
- **上下文压缩**：Token 占比超过阈值时自动 LLM 摘要压缩 / TODO：目前实现的还是很简单
- **轮次回退**：基于 Git 提交的对话轮次回退（`/revert`）
- **项目记忆**：AST 扫描代码库，构建函数级记忆索引，每轮自动注入相关上下文
- **多 LLM 支持**：内置智谱 GLM 客户端，架构支持扩展 Anthropic 等其他提供商
- **Windows 兼容**：自动处理 GBK / UTF-8 编码问题

---

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

或直接安装依赖：

```bash
pip install -r requirments.txt
```

### 2. 配置

复制示例配置并填入你的 API Key：

```bash
cp config/config.example.yaml config/config.yaml
```

编辑 `config/config.yaml`，将 `api_key` 替换为真实 Key：

```yaml
llm:
  provider: zhipu
  api_key: "YOUR_API_KEY_HERE"
```

也可以通过环境变量设置（优先级高于配置文件）：

```bash
export ZHIPU_API_KEY="your_key"     # 智谱 GLM
export GLM_API_KEY="your_key"       # 同上
export ANTHROPIC_API_KEY="your_key" # Anthropic Claude
```

### 3. 启动

```bash
# 交互模式
python -m src.main

# 或通过 pyproject.toml 安装后
myvibe

# 一次性执行（headless 模式）
myvibe -p "用 Python 写一个快速排序并附上测试"

# 继续上次会话
myvibe --continue

# 恢复指定会话
myvibe --resume <session_id>

# 指定模型
myvibe --model glm-4-flash

# 指定工作目录
myvibe --cwd /path/to/project
```

---

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/init` | 扫描项目并用 LLM 生成 `MyVibe.md` 项目记忆文件 |
| `/clear` | 清除当前对话历史 |
| `/compact` | LLM 摘要压缩对话历史（节省 Token） |
| `/cost` | 显示当前会话 Token 用量和费用统计 |
| `/sessions` | 列出所有历史会话 |
| `/history` | 查看当前会话的对话轮次 Git 提交历史 |
| `/revert` | 交互式选择回退到某一轮次（同时回退文件和对话上下文） |
| `/plan` | 切换计划模式（不执行工具，只输出计划）|
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
│   │   ├── base_agent.py       # 抽象基类
│   │   ├── coding_agent.py     # 核心 Agentic 循环 + 权限管理
│   │   ├── conversation_recorder.py  # 完整对话历史 JSONL 记录
│   │   ├── project_init.py     # /init 项目记忆生成
│   │   └── state.py            # 运行时状态 + 会话管理
│   ├── llm/
│   │   ├── base_client.py      # LLM 客户端抽象基类
│   │   ├── zhipu_client.py     # 智谱 GLM 实现
│   │   ├── client.py           # 工厂函数
│   │   └── prompts.py          # 系统提示词构建
│   ├── tools/
│   │   ├── base_tool.py        # 工具基类 + 注册表
│   │   ├── file.py             # 文件读写编辑搜索
│   │   ├── shell.py            # Shell 执行（注入检测）
│   │   ├── git.py              # Git 状态/Diff/提交/回退
│   │   ├── lsp.py              # LSP 代码分析（stub）
│   │   ├── context_tools.py    # 项目上下文工具
│   │   └── memory_tools.py     # 记忆系统工具
│   ├── context/
│   │   ├── context_manager.py  # 文件摘要与上下文管理
│   │   ├── file_summary.py     # 文件摘要生成
│   │   └── parsers/            # Python / 通用代码解析器
│   ├── memory/
│   │   ├── memory_manager.py   # 记忆系统入口
│   │   ├── ast_analyzer.py     # AST 函数扫描
│   │   ├── storage.py          # 记忆存储（JSON）
│   │   ├── models.py           # 数据模型
│   │   └── health_check.py     # 记忆健康检查
│   ├── completer/
│   │   └── ...                 # prompt_toolkit 自动补全
│   ├── logger/
│   │   ├── structured_logger.py  # 双轨日志（JSONL + Rich 控制台）
│   │   └── log_formats.py
│   └── utils/
│       ├── path.py             # 安全路径解析（防路径遍历）
│       └── diff.py             # Unified diff 生成
├── config/
│   ├── config.example.yaml    # 配置模板（提交到仓库）
│   └── config.yaml            # 实际配置（含 Key，不提交）
├── DOCS/
│   ├── Plan.MD                 # 架构设计文档
│   ├── TODO_LIST.MD            # 待办清单
│   └── 上下文管理.MD           # 上下文管理设计
├── OPTIMIZATIONS.md            # 优化历史记录
├── pyproject.toml
└── requirments.txt
```

---

## 架构概述

### Agentic 循环

```
用户输入
  → AppendUser() → 检查是否需要压缩上下文
  → BuildSystemPrompt()（含项目记忆注入）
  ↓
while True:
  → LLM.stream_chat()（流式输出）
  → AppendAssistant() + 保存会话
  ↓
  stop_reason == "tool_use"?
    → 并行执行工具（ThreadPoolExecutor, max=4）
    → 权限检查 → 执行 → 返回结果
  stop_reason == "end_turn"?
    → 返回最终文本
```

### 工具权限层级

| 级别 | 行为 | 默认包含 |
|------|------|---------|
| `auto_allow` | 直接执行 | 文件读取、Git 查询、代码搜索 |
| `require_confirm` | 弹出确认框 | 文件写入、Shell、Git 提交 |
| `deny` | 永久拒绝 | `rm -rf /`、`format` 等危险命令 |

---

## 配置说明

详见 `config/config.example.yaml`，主要配置项：

| 配置项 | 说明 |
|--------|------|
| `llm.provider` | LLM 提供商：`zhipu` 或 `anthropic` |
| `llm.model` | 模型名称（如 `glm-4.7`、`glm-4-flash`） |
| `llm.api_key` | API Key（推荐用环境变量替代） |
| `agent.max_tool_workers` | 并行工具 Worker 数量 |
| `agent.context_compress_threshold` | 触发上下文压缩的 Token 占比阈值 |
| `permissions.*` | 工具权限配置 |
| `shell.timeout_ms` | Shell 命令超时（毫秒） |

---

## 依赖

| 包 | 用途 |
|----|------|
| `anthropic` | Anthropic SDK（可选，支持 Claude 模型） |
| `rich` | 终端美化输出 |
| `pyyaml` | 配置文件解析 |
| `gitpython` | Git 操作 |
| `prompt_toolkit` | 交互式 CLI，自动补全 |
| `pathspec` | `.gitignore` 规则匹配 |
| `requests` | HTTP 请求 |

---

## License

MIT
