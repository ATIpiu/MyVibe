"""文件摘要：生成、格式化单个文件的函数摘要。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .parsers.language_map import get_language, LANGUAGE_MAP
from .parsers.python_parser import FunctionInfo


@dataclass
class FileSummary:
    """单个文件的摘要数据。"""
    file_path: str
    language: str
    functions: list[FunctionInfo]
    line_count: int
    file_hash: str   # sha256 前16位


def generate_summary(file_path: str) -> Optional[FileSummary]:
    """调用对应 parser，生成文件摘要。

    Args:
        file_path: 文件路径

    Returns:
        FileSummary，解析失败时返回 None
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None

    ext = path.suffix.lower()
    language = get_language(ext)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    line_count = len(content.splitlines())

    if language == "python":
        from .parsers.python_parser import parse_file
        functions = parse_file(file_path)
    else:
        from .parsers.generic_parser import parse_file
        functions = parse_file(file_path, language)

    return FileSummary(
        file_path=str(path),
        language=language,
        functions=functions,
        line_count=line_count,
        file_hash=file_hash,
    )


def format_function_entry(func: FunctionInfo) -> str:
    """单个函数的四行摘要格式。"""
    lines = [func.signature]
    if func.description:
        lines.append(f"  {func.description}")
    examples = []
    if func.input_example:
        examples.append(f"  输入: {func.input_example[:80]}")
    if func.output_example:
        examples.append(f"  输出: {func.output_example[:80]}")
    lines.extend(examples)
    return "\n".join(lines)


def format_summary(summary: FileSummary, max_chars: int = 2000) -> str:
    """将 FileSummary 格式化为高密度可读字符串。

    Args:
        summary: 文件摘要
        max_chars: 最大字符数，超出时截断函数列表

    Returns:
        格式化后的摘要字符串
    """
    # 尝试显示相对路径
    try:
        rel_path = Path(summary.file_path).name
        # 若路径较短则显示更多层级
        parts = Path(summary.file_path).parts
        if len(parts) > 2:
            rel_path = "/".join(parts[-3:])
    except Exception:
        rel_path = summary.file_path

    lang_display = summary.language.capitalize()
    func_count = len(summary.functions)
    header = (
        f"文件: {rel_path}  [{lang_display} | {func_count}个函数 | {summary.line_count}行]\n"
        + "─" * 50
    )

    if not summary.functions:
        return header + "\n(未检测到函数定义)"

    entries = []
    total_chars = len(header)
    for func in summary.functions:
        entry = format_function_entry(func)
        if total_chars + len(entry) + 2 > max_chars:
            remaining = func_count - len(entries)
            entries.append(f"... (还有 {remaining} 个函数，使用 get_function_code 查看详情)")
            break
        entries.append(entry)
        total_chars += len(entry) + 2

    return header + "\n" + "\n\n".join(entries)


def is_stale(summary: FileSummary, file_path: str) -> bool:
    """检查文件 hash 是否变化（需要重新解析）。

    Args:
        summary: 已有摘要
        file_path: 文件路径

    Returns:
        True 表示文件已变化，缓存失效
    """
    path = Path(file_path)
    if not path.exists():
        return True
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        current_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        return current_hash != summary.file_hash
    except Exception:
        return True
