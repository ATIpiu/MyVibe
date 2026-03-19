"""Python AST 分析引擎：提取函数名、一句话描述、调用关系。

精简原则：
  - 只提取 qualname、purpose（docstring 第一行）
  - 嵌套函数（定义在函数内部的函数）不收录
  - 调用关系以 calls_map 独立返回，不存入 FunctionData
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from .models import FunctionData, ModuleData


class AstAnalyzer:
    """分析 Python 源文件，提取函数基本信息 + 调用关系。"""

    def analyze_file(
        self,
        file_path: Path,
        relative_to: Optional[Path] = None,
    ) -> tuple[ModuleData, dict[str, list[str]]]:
        """解析文件，返回 (ModuleData, calls_map)。

        calls_map 格式：{qualname: [callee_key（module:qualname）, ...]}
        """
        source = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return ModuleData(purpose="语法错误，无法解析"), {}

        rel_path = str(file_path.relative_to(relative_to)) if relative_to else file_path.name
        rel_path = rel_path.replace("\\", "/")

        imports = _extract_imports(tree)

        functions: dict[str, FunctionData] = {}
        calls_map: dict[str, list[str]] = {}
        _collect_functions(tree, rel_path, imports, functions, calls_map, class_name="")

        module_docstring = ast.get_docstring(tree) or ""
        purpose = module_docstring.split("\n")[0].strip() if module_docstring else ""

        return ModuleData(purpose=purpose, functions=functions), calls_map

    def get_function_ranges(self, file_path: Path) -> dict[str, tuple[int, int]]:
        """返回 {qualname: (start_line, end_line)}，行号 1-indexed，每次实时 parse。"""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            return {}
        ranges: dict[str, tuple[int, int]] = {}
        _collect_ranges(tree, ranges, class_name="")
        return ranges

    def get_function_source(self, file_path: Path, qualname: str) -> Optional[str]:
        """精确提取指定函数/方法的完整源码。"""
        source = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        parts = qualname.split(".")
        func_name = parts[-1]
        class_name = parts[0] if len(parts) > 1 else ""

        lines = source.splitlines()
        node = _find_function_node(tree, func_name, class_name)
        if node is None:
            return None

        start = node.lineno - 1
        end = node.end_lineno if hasattr(node, "end_lineno") else _estimate_end(lines, start)
        return "\n".join(lines[start:end])


# ─────────────────────── 内部工具函数 ───────────────────────


def _collect_functions(
    tree: ast.AST,
    module_path: str,
    imports: dict[str, str],
    functions: dict[str, FunctionData],
    calls_map: dict[str, list[str]],
    class_name: str,
) -> None:
    """收集顶层函数及类方法（不递归进函数内部，嵌套函数不收录）。"""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            _collect_functions(node, module_path, imports, functions, calls_map, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{class_name}.{node.name}" if class_name else node.name
            docstring = ast.get_docstring(node) or ""
            purpose = docstring.split("\n")[0].strip() if docstring else ""
            functions[qualname] = FunctionData(purpose=purpose)
            # 提取该函数的调用关系（不进入嵌套函数体）
            calls_map[qualname] = _extract_calls(node, imports, module_path)


def _extract_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    imports: dict[str, str],
    module_path: str,
) -> list[str]:
    """静态提取函数直接调用的其他函数 key，跳过嵌套函数体。"""
    calls: set[str] = set()

    for child in ast.walk(node):
        if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not isinstance(child, ast.Call):
            continue
        func = child.func

        if isinstance(func, ast.Name):
            name = func.id
            if name in imports:
                calls.add(imports[name])
            else:
                calls.add(f"{module_path}:{name}")

        elif isinstance(func, ast.Attribute):
            method = func.attr
            if isinstance(func.value, ast.Name):
                obj_name = func.value.id
                if obj_name in imports:
                    base_module = imports[obj_name]
                    calls.add(f"{base_module}:{method}")

    return list(calls)


def _extract_imports(tree: ast.AST) -> dict[str, str]:
    """提取所有 import，返回 {本地名: 来源模块路径}。"""
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                mapping[local] = alias.name.replace(".", "/") + ".py"
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").replace(".", "/")
            for alias in node.names:
                local = alias.asname or alias.name
                if module:
                    mapping[local] = f"{module}.py:{alias.name}"
                else:
                    mapping[local] = alias.name
    return mapping


def _find_function_node(
    tree: ast.AST, func_name: str, class_name: str
) -> Optional[ast.FunctionDef]:
    for node in ast.walk(tree):
        if class_name and isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name == func_name:
                        return child
        elif not class_name and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return node
    return None


def _collect_ranges(tree: ast.AST, ranges: dict, class_name: str) -> None:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            _collect_ranges(node, ranges, class_name=node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{class_name}.{node.name}" if class_name else node.name
            ranges[qualname] = (node.lineno, getattr(node, "end_lineno", node.lineno))


def _estimate_end(lines: list[str], start: int) -> int:
    """启发式估计函数结束行（无 end_lineno 时的回退）。"""
    if start + 1 >= len(lines):
        return len(lines)
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= base_indent and stripped:
            return i
    return len(lines)
