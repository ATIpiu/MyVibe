"""项目初始化：/init 命令由主 CodingAgent 主动探索项目后生成 MyVibe.md。

MyVibe.md 是项目维度的记忆，类似 Claude Code 的 CLAUDE.md：
- 由 AI 通过工具调用自动生成，用户可手动编辑
- 每次对话开始时自动注入系统提示词
- 记录项目架构、模块职责、技术栈、约定、常用命令等
"""
from __future__ import annotations

from pathlib import Path

MYVIBE_FILENAME = "MyVibe.md"

# 指示 CodingAgent 主动探索项目并写入 MyVibe.md 的 prompt
_INIT_AGENT_PROMPT = """\
请对当前项目进行全面的架构分析，然后用 write_file 工具将结果写入 {myvibe_path}。

## 分析步骤（请按顺序执行）

1. `read_memory(scope='all')` — 获取所有模块与函数索引，了解项目全貌
2. 读取项目配置文件：`pyproject.toml` / `setup.py` / `requirements.txt` / `config/` 目录等
3. 如有 README，读取它
4. 读取主入口文件（如 `main.py`、`__main__.py`、`app.py`、`src/main.py` 等）
5. 对核心模块用 `read_memory(scope='module', module_path=...)` 深入了解其职责
6. 通过 `git_log` / `git_status` 了解版本与近期改动

## MyVibe.md 写入要求

内容**必须真实反映项目现状**，覆盖以下章节（未知或不适用的可省略）：

```markdown
# 项目记忆

## 项目简介
（一句话描述：这是什么，解决什么问题）

## 整体架构
（描述各层/子系统的分工，核心模块间的依赖关系与数据流向，
 尽量用列表或短段落说清楚"谁调用谁、数据怎么流"）

## 模块说明
（每个模块/目录一行，格式：`路径` — 职责 · 核心类/函数）

## 技术栈
（语言版本、主要框架/库及其用途）

## 入口与启动流程
（如何运行，主入口在哪，启动时发生了什么）

## 重要约定
（编码规范、架构决策、命名规则、必须遵守的约束）

## 常用命令
（运行、测试、构建、调试命令）

## 注意事项
（已知问题、特殊限制、开发陷阱）

---
*此文件由 AI 自动生成，可手动编辑。每次对话开始时自动加载为项目上下文。*
```

完成分析后，**直接调用 write_file 写入文件，不要询问用户确认**。
"""


def myvibe_path(cwd: str) -> Path:
    """返回 MyVibe.md 的绝对路径。"""
    return Path(cwd) / MYVIBE_FILENAME


def load_myvibe(cwd: str) -> str:
    """读取 MyVibe.md，不存在时返回空字符串。"""
    path = myvibe_path(cwd)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def get_init_prompt(cwd: str) -> "tuple[bool, str]":
    """检查是否需要初始化，并返回发给 Agent 的 prompt。

    Returns:
        (already_exists, prompt_str)
        already_exists=True 表示 MyVibe.md 已存在，prompt_str 为空。
    """
    path = myvibe_path(cwd)
    if path.exists():
        return True, ""
    return False, _INIT_AGENT_PROMPT.format(myvibe_path=str(path))
