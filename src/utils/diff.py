"""diff 工具：生成 unified diff 和 Rich 彩色差异显示。"""
import difflib
from typing import Union


def generate_unified_diff(
    original: str,
    modified: str,
    file_path: str,
    context_lines: int = 3,
) -> str:
    """生成标准 unified diff 字符串。

    Args:
        original: 原始内容
        modified: 修改后内容
        file_path: 文件路径（用于 diff 头部显示）
        context_lines: 差异上下文行数

    Returns:
        unified diff 格式字符串
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=context_lines,
    )
    return "".join(diff)


def generate_rich_diff(
    original: str,
    modified: str,
    file_path: str,
) -> str:
    """生成带 Rich markup 的彩色 diff 字符串。

    Args:
        original: 原始内容
        modified: 修改后内容
        file_path: 文件路径

    Returns:
        包含 Rich markup 的彩色 diff 字符串
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
    ))

    result_parts = []
    for line in diff_lines:
        line_stripped = line.rstrip("\n")
        if line_stripped.startswith("+++") or line_stripped.startswith("---"):
            result_parts.append(f"[bold white]{line_stripped}[/bold white]")
        elif line_stripped.startswith("+"):
            result_parts.append(f"[bold green]{line_stripped}[/bold green]")
        elif line_stripped.startswith("-"):
            result_parts.append(f"[bold red]{line_stripped}[/bold red]")
        elif line_stripped.startswith("@@"):
            result_parts.append(f"[cyan]{line_stripped}[/cyan]")
        else:
            result_parts.append(f"[dim]{line_stripped}[/dim]")

    return "\n".join(result_parts)


def count_diff_stats(original: str, modified: str) -> dict:
    """统计差异的增删行数。

    Args:
        original: 原始内容
        modified: 修改后内容

    Returns:
        包含 added/removed/unchanged 的统计字典
    """
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, original_lines, modified_lines)

    added = removed = unchanged = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            unchanged += i2 - i1
        elif op == "insert":
            added += j2 - j1
        elif op == "delete":
            removed += i2 - i1
        elif op == "replace":
            removed += i2 - i1
            added += j2 - j1

    return {"added": added, "removed": removed, "unchanged": unchanged}
