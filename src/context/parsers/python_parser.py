"""Python 文件解析器：使用 ast 模块提取函数/方法信息。"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FunctionInfo:
    """单个函数/方法的元数据。"""
    name: str
    signature: str          # "def foo(x: int, y: str = 'a') -> bool"
    description: str        # docstring 第一行（或空）
    input_example: str      # 从 docstring 解析（或空）
    output_example: str     # 从 docstring 解析（或空）
    start_line: int
    end_line: int
    class_name: str = ""    # 所属类（顶层函数为空）
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)


def parse_file(file_path: str) -> list[FunctionInfo]:
    """用 ast 解析 .py 文件，提取所有函数和方法。

    Args:
        file_path: Python 文件路径

    Returns:
        FunctionInfo 列表（含类方法，class_name 为所属类名）
    """
    path = Path(file_path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    except Exception:
        return []

    results: list[FunctionInfo] = []
    source_lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info = _build_function_info(item, source_lines, class_name)
                    results.append(info)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 检查是否是顶层函数（父节点是 Module）
            parent_is_class = any(
                isinstance(parent, ast.ClassDef) and node in ast.walk(parent)
                for parent in ast.walk(tree)
                if isinstance(parent, ast.ClassDef)
            )
            if not parent_is_class:
                info = _build_function_info(node, source_lines, "")
                results.append(info)

    # 按行号排序，去重
    seen = set()
    unique = []
    for f in sorted(results, key=lambda x: x.start_line):
        key = (f.name, f.class_name, f.start_line)
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


def _build_function_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    class_name: str,
) -> FunctionInfo:
    """从 AST 节点构建 FunctionInfo。"""
    signature = _get_signature(node, source_lines)
    docstring_raw = _parse_docstring(node)
    description = _get_first_line(docstring_raw)
    input_ex, output_ex = _infer_io_examples(docstring_raw)
    decorators = [ast.unparse(d) for d in node.decorator_list]

    return FunctionInfo(
        name=node.name,
        signature=signature,
        description=description,
        input_example=input_ex,
        output_example=output_ex,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        class_name=class_name,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=decorators,
    )


def _get_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> str:
    """从源码中提取完整函数签名（第一行）。"""
    line_idx = node.lineno - 1
    if 0 <= line_idx < len(source_lines):
        line = source_lines[line_idx].strip()
        # 去掉末尾的冒号
        if line.endswith(":"):
            line = line[:-1]
        return line
    return f"def {node.name}(...)"


def _parse_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """从 AST node 提取完整 docstring（如有）。"""
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        return node.body[0].value.value
    return ""


def _get_first_line(docstring: str) -> str:
    """取 docstring 第一个非空行作为描述。"""
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _infer_io_examples(docstring: str) -> tuple[str, str]:
    """从 docstring 中提取输入/输出示例。

    Returns:
        (input_example, output_example) 元组
    """
    input_ex = ""
    output_ex = ""

    # 匹配 Args: / 输入: 后的示例
    input_patterns = [
        r"(?:Args?|输入|Input)[：:]\s*\n((?:\s+.+\n?)+)",
        r"(?:Example|示例)[：:]\s*\n((?:\s+.+\n?)+)",
    ]
    for pat in input_patterns:
        m = re.search(pat, docstring, re.MULTILINE | re.IGNORECASE)
        if m:
            input_ex = m.group(1).strip()[:100]
            break

    # 匹配 Returns: / 输出: 后的示例
    output_patterns = [
        r"(?:Returns?|输出|Output)[：:]\s*\n((?:\s+.+\n?)+)",
        r"(?:Yields?)[：:]\s*\n((?:\s+.+\n?)+)",
    ]
    for pat in output_patterns:
        m = re.search(pat, docstring, re.MULTILINE | re.IGNORECASE)
        if m:
            output_ex = m.group(1).strip()[:100]
            break

    return input_ex, output_ex


def _get_type_hints(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """提取函数参数和返回值类型注解字符串。"""
    parts = []
    for arg in node.args.args:
        if arg.annotation:
            try:
                parts.append(f"{arg.arg}: {ast.unparse(arg.annotation)}")
            except Exception:
                parts.append(arg.arg)
        else:
            parts.append(arg.arg)

    returns = ""
    if node.returns:
        try:
            returns = f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass

    return f"({', '.join(parts)}){returns}"


def extract_function_code(
    file_path: str,
    func_name: str,
    class_name: Optional[str] = None,
) -> str:
    """精确提取单个函数的完整源码（含缩进）。

    Args:
        file_path: Python 文件路径
        func_name: 函数名
        class_name: 所属类名（方法时需要），None 表示顶层函数

    Returns:
        函数的完整源码字符串，未找到时返回空字符串
    """
    path = Path(file_path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except Exception:
        return ""

    source_lines = source.splitlines()
    target_node = None

    if class_name:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                        target_node = item
                        break
                if target_node:
                    break
    else:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                target_node = node
                break

    if target_node is None:
        return ""

    start = target_node.lineno - 1
    end = target_node.end_lineno or (start + 1)
    func_lines = source_lines[start:end]
    return "\n".join(func_lines)
