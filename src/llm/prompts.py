"""系统提示词构建：组装 system prompt、工具描述、memory 上下文。"""
from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def detect_platform() -> str:
    """检测当前操作系统和 Shell 类型，返回简短描述。"""
    system = platform.system()
    if system == "Windows":
        if "PSModulePath" in os.environ:
            shell = "PowerShell"
        else:
            shell = "CMD（命令提示符）"
        return f"Windows {platform.release()} / {shell}"
    elif system == "Darwin":
        shell = os.path.basename(os.environ.get("SHELL", "zsh"))
        return f"macOS {platform.mac_ver()[0]} / {shell}"
    else:
        shell = os.path.basename(os.environ.get("SHELL", "bash"))
        return f"Linux / {shell}"


SYSTEM_PROMPT_TEMPLATE = """你是一个专业的 AI 编程助手，运行在用户的本地机器上。

## 当前环境
- 工作目录: {cwd}
- 时间: {timestamp}
- 可用工具: {tool_count} 个
- 操作系统: {platform_info}

> **Shell 注意**：请根据上方操作系统使用正确的命令语法。
> Windows PowerShell：用 `Get-Location`/`Set-Location`/`Get-ChildItem`，不用 `pwd`/`cd`/`ls`。
> Windows CMD：用 `cd`/`dir`，不用 `pwd`/`ls`。
> Linux/macOS：用 `pwd`/`ls`/`cd`。

## 你的能力
你可以读写文件、执行命令、分析代码、使用 git。   
你有访问完整项目的能力，可以独立完成复杂的编程任务。

## 代码探索策略（重要）
按以下顺序使用工具，从全局到精确：

1. `read_file(scope='file', files=[…])` → 某模块下的所有函数签名与描述
2. `read_file(scope='function', function_key=…)` → 指定函数完整源码 + callers + callees（通常这步就够了）
3. `read_file(scope='overview')` → 全项目模块总览（只有在需要了解整体项目的时候调用）
4. `find_symbol`              → 已知变量名/调用名时直接定位到 module:qualname
5. `grep_files`               → 按字面量/正则在项目内搜索
6. `rebuild_index`            → 初始化或重建代码索引（首次使用或大量修改后）

## 编码规范（必须遵守）
新建 .py 文件时，**文件第一行必须是一句话模块说明**（docstring），供记忆系统索引：
```python"""+r"""<一句话描述该文件的职责>"""+""""\n```
每个函数/方法，**函数体第一行必须是一句话描述**（docstring 第一行），格式：
```python
def my_func(...):
    """+r"""<一句话描述该文件的职责>"""+""""\n```
这是代码记忆系统的数据来源，缺少描述会导致 AI 无法理解函数用途。

## 工具使用规则
- 路径操作：使用绝对路径，所有文件路径会自动安全检查
- 编辑文件：old_string 必须在文件中唯一出现，否则提供更多上下文
- 执行命令：危险命令会触发权限确认，请提供清晰的 description 参数
- 并行工具：无依赖关系的工具调用会并行执行，注意不要产生写冲突
- 代码定位优先用结构化索引：`read_file` / `find_symbol` 比按行读文件更精准，
  能直接返回函数源码与调用关系。

## 输出风格
- 保持简洁，直接给出结论和操作
- 代码修改时说明改动原因
- 遇到不确定时主动询问，不要猜测用户意图
- **已确认目标时直接执行**：若已通过工具确认了目标文件、函数和改动内容，立即调用 edit_file/write_file，不需要再次确认。"主动询问"适用于任务本身有歧义，而非执行前的犹豫。

{myvibe_context}{memory_context}{proactive_memory}{plan_mode_section}
## 可用工具（共 {tool_count} 个）
工具 schema 已通过 API 参数传递，按需调用即可。
"""

PLAN_MODE_BLOCK = """
## 计划模式（已激活）

> **工具限制**：当前只能使用只读工具（read_file / search_in_file / git_status / git_diff /
> lsp_hover 等），**禁止**调用 write_file / edit_file / shell / git_commit。
> 用户确认计划后，系统将自动解除限制并重新运行。

收到用户任务后，请按以下步骤操作：

1. **收集信息**：使用只读工具阅读相关代码，了解现状。
2. **分析不确定性**：判断是否存在歧义、可选技术路线或缺少关键信息。
3. **若任务明确**：输出编号步骤计划，末尾写：
   > "请确认是否按此计划执行？（回复 **y** 或直接 Enter 开始）"
4. **若存在多个可选方案**：按如下格式逐个列出问题：

**问题：[问题描述]**
1. [方案 A]
2. [方案 B]
3. [方案 C]（可选）
4. 自定义：请输入你的具体想法

> 用户回复数字（如 "2"）快速选择，或输入自定义描述。
> 所有问题确认完毕后，输出最终执行计划，等待用户最后确认。
> **不要提前执行任何修改操作。**
"""


def build_system_prompt(
    tool_descriptions: str,
    cwd: str,
    memory_context: str = "",
    timestamp: Optional[str] = None,
    tool_count: int = 0,
    proactive_memory: str = "",
    myvibe_context: str = "",
    platform_info: str = "",
    plan_mode: bool = False,
) -> str:
    """填充系统提示词模板。

    Args:
        tool_descriptions: 工具 schema 的人类可读描述
        cwd: 当前工作目录
        memory_context: AGENT.md 内容（可选）
        timestamp: ISO8601 时间戳，None 时自动生成
        tool_count: 工具总数
        proactive_memory: 主动记忆注入内容（按用户输入检索）
        myvibe_context: MyVibe.md 项目记忆内容（可选）

    Returns:
        完整系统提示词字符串
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    myvibe_section = ""
    if myvibe_context.strip():
        myvibe_section = f"\n## 项目记忆（MyVibe.md）\n{myvibe_context.strip()}\n"

    memory_section = ""
    if memory_context.strip():
        memory_section = f"\n## 全局记忆（AGENT.md）\n{memory_context.strip()}\n"

    proactive_section = ""
    if proactive_memory.strip():
        proactive_section = f"\n{proactive_memory.strip()}\n"

    plan_mode_section = PLAN_MODE_BLOCK if plan_mode else ""

    return SYSTEM_PROMPT_TEMPLATE.format(
        cwd=cwd,
        timestamp=timestamp,
        tool_count=tool_count,
        tool_descriptions=tool_descriptions,
        myvibe_context=myvibe_section,
        memory_context=memory_section,
        proactive_memory=proactive_section,
        plan_mode_section=plan_mode_section,
        platform_info=platform_info or detect_platform(),
    )


def build_tool_descriptions(tools_schema: list[dict]) -> str:
    """将 schema list 转为人类可读的工具列表字符串。

    Args:
        tools_schema: Anthropic API tools 数组

    Returns:
        格式化后的工具描述字符串
    """
    lines = []
    for tool in tools_schema:
        name = tool.get("name", "?")
        desc = tool.get("description", "").split("\n")[0][:100]
        lines.append(f"- `{name}`: {desc}")
    return "\n".join(lines)


def load_memory_context(
    global_path: str,
    project_path: Optional[str] = None,
) -> str:
    """读取全局和项目级 AGENT.md，合并为上下文字符串。

    Args:
        global_path: 全局 AGENT.md 路径（如 ~/.claude/AGENT.md）
        project_path: 项目级 AGENT.md 路径（可选）

    Returns:
        合并后的 memory 内容字符串
    """
    parts = []

    for path_str in [global_path, project_path]:
        if not path_str:
            continue
        path = Path(path_str).expanduser()
        if path.exists() and path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
            except Exception:
                pass

    return "\n\n---\n\n".join(parts) if parts else ""
