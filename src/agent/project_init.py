"""项目初始化：首次运行时生成 MyVibe.md 项目记忆文件。

MyVibe.md 是项目维度的记忆，类似 Claude Code 的 CLAUDE.md：
- 由 AI 首次自动生成，用户可手动编辑
- 每次对话开始时自动注入系统提示词
- 记录项目简介、技术栈、约定、常用命令等
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.client import LLMClient

MYVIBE_FILENAME = "MyVibe.md"

_README_NAMES = [
    "README.md", "README.rst", "README.txt",
    "readme.md", "Readme.md", "README",
]

_GENERATE_PROMPT = """\
你是一个项目分析助手。请根据以下项目信息，生成一个 MyVibe.md 项目记忆文件。

项目路径：{cwd}

{project_info}

请用中文生成结构清晰的 MyVibe.md，严格按照以下模板，未知信息留空或省略整个小节：

# 项目记忆

## 项目简介
（一句话描述项目的用途和目标）

## 技术栈
（主要编程语言、框架、关键依赖）

## 项目结构
（关键目录和文件的说明，保持简洁）

## 重要约定
（编码规范、命名规则、架构决策、需要注意的规则）

## 常用命令
（如何运行、测试、构建、调试项目）

## 注意事项
（已知问题、特殊限制、开发陷阱）

---
*此文件由 AI 自动生成，可手动编辑。每次对话开始时自动加载为项目上下文。*
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


def _find_readme(cwd: str) -> Optional[str]:
    """查找并读取 README 文件，返回内容（最多 5000 字符）。"""
    for name in _README_NAMES:
        path = Path(cwd) / name
        if path.exists() and path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:5000]
            except OSError:
                pass
    return None


def _build_project_info(cwd: str, memory_manager) -> str:
    """收集项目信息：优先 README，其次记忆模块扫描结果。"""
    readme = _find_readme(cwd)
    if readme:
        return f"## README 内容\n\n{readme}"

    # 没有 README → 用记忆模块扫描项目结构
    try:
        memory_manager.sync()
        all_memory = memory_manager.read_all()
        if not all_memory:
            return ""

        lines = [f"## 项目代码结构（共 {len(all_memory)} 个模块）\n"]
        for mod_path, mod_data in sorted(all_memory.items()):
            func_names = list(mod_data.functions.keys())[:6]
            func_str = ", ".join(func_names)
            if len(mod_data.functions) > 6:
                func_str += f" 等 {len(mod_data.functions)} 个"
            desc = f"  —  {mod_data.purpose}" if mod_data.purpose else ""
            lines.append(f"- `{mod_path}`{desc}  [{func_str}]")
        return "\n".join(lines)
    except Exception:
        return ""


def initialize_project(
    llm_client: "LLMClient",
    memory_manager,
    cwd: str,
    console,
) -> bool:
    """首次运行时初始化项目记忆，生成 MyVibe.md。

    Args:
        llm_client: LLM 客户端（用于生成文件内容）
        memory_manager: 记忆管理器（无 README 时用于扫描项目）
        cwd: 项目根目录
        console: Rich Console 实例（用于打印进度）

    Returns:
        True 表示新生成了 MyVibe.md，False 表示已存在跳过。
    """
    path = myvibe_path(cwd)
    if path.exists():
        return False  # 已存在，无需初始化

    console.print("\n[bold cyan]首次运行，正在初始化项目记忆...[/bold cyan]")

    # 收集项目信息
    readme = _find_readme(cwd)
    if readme:
        console.print("[dim]  已找到 README，作为项目信息来源[/dim]")
        project_info = f"## README 内容\n\n{readme}"
    else:
        console.print("[dim]  未找到 README，正在扫描代码结构...[/dim]")
        project_info = _build_project_info(cwd, memory_manager)
        if not project_info:
            console.print("[dim]  代码结构扫描完成（暂无函数记录）[/dim]")

    # 用 LLM 生成 MyVibe.md
    console.print("[dim]  正在生成 MyVibe.md...[/dim]")
    prompt = _GENERATE_PROMPT.format(
        cwd=cwd,
        project_info=project_info or "（项目信息暂时不可用，请根据项目路径推断）",
    )

    try:
        response = llm_client.stream_chat(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个专业的项目文档助手。请生成简洁、实用的项目记忆文件，用中文，内容真实可信。",
            tools=[],
        )
        content = response.text_content
        if content and content.strip():
            path.write_text(content.strip() + "\n", encoding="utf-8")
            console.print(f"[green]✓ 已生成 {MYVIBE_FILENAME}（位于 {path}）[/green]")
            console.print("[dim]  你可以随时编辑此文件来更新项目记忆。[/dim]")
            return True
        else:
            console.print("[yellow]  LLM 未返回内容，跳过生成[/yellow]")
    except Exception as e:
        console.print(f"[yellow]  生成 MyVibe.md 失败：{e}[/yellow]")

    return False
