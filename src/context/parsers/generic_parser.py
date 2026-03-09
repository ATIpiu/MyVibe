"""通用语言解析器：正则兜底，适用于 Java/Go/TS 等语言。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .language_map import get_function_pattern, get_language
from .python_parser import FunctionInfo


def parse_file(file_path: str, language: Optional[str] = None) -> list[FunctionInfo]:
    """正则解析任意语言文件，提取函数名和行号。

    Args:
        file_path: 文件路径
        language: 语言标识符，None 时根据扩展名自动推断

    Returns:
        FunctionInfo 列表（description/examples 为空）
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if language is None:
        language = get_language(ext)

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    lines = source.splitlines()
    pattern = get_function_pattern(ext)

    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(r"(?:def|func|function|fn)\s+(\w+)\s*[(<]")

    results: list[FunctionInfo] = []
    for line_idx, line in enumerate(lines):
        m = regex.search(line)
        if not m:
            continue

        # 取第一个非空捕获组作为函数名
        func_name = next((g for g in m.groups() if g), None)
        if not func_name:
            continue

        # 跳过常见假阳性
        if func_name in ("if", "for", "while", "switch", "class", "return", "import"):
            continue

        end_line = _detect_function_end(lines, line_idx)

        results.append(FunctionInfo(
            name=func_name,
            signature=line.strip()[:200],
            description="",
            input_example="",
            output_example="",
            start_line=line_idx + 1,
            end_line=end_line,
        ))

    return results


def _detect_function_end(lines: list[str], start: int) -> int:
    """通过括号匹配/缩进启发式检测函数结束行。

    Args:
        lines: 文件所有行
        start: 函数定义起始行索引（0-based）

    Returns:
        函数结束行号（1-based）
    """
    if start >= len(lines):
        return start + 1

    # 方法一：花括号匹配（C/Java/Go/Rust/TS 等）
    brace_depth = 0
    found_open = False
    for i in range(start, min(start + 300, len(lines))):
        line = lines[i]
        for ch in line:
            if ch == "{":
                brace_depth += 1
                found_open = True
            elif ch == "}":
                brace_depth -= 1
                if found_open and brace_depth <= 0:
                    return i + 1  # 1-based

    # 方法二：缩进检测（Python 风格兜底）
    if start + 1 < len(lines):
        base_indent = len(lines[start]) - len(lines[start].lstrip())
        for i in range(start + 1, min(start + 200, len(lines))):
            stripped = lines[i].strip()
            if not stripped:
                continue
            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent <= base_indent and stripped:
                return i  # 1-based（上一行结束）

    return min(start + 50, len(lines))


def extract_function_code(
    file_path: str,
    start_line: int,
    end_line: int,
) -> str:
    """基于行号提取函数体源码。

    Args:
        file_path: 文件路径
        start_line: 起始行号（1-based）
        end_line: 结束行号（1-based，含）

    Returns:
        函数源码字符串
    """
    path = Path(file_path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        return "\n".join(lines[start:end])
    except Exception:
        return ""
