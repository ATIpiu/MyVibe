"""Python AST 分析引擎：提取函数名、一句话描述、调用关系。

精简原则：
  - 只提取 qualname、purpose（docstring 第一行）
  - 嵌套函数/类（定义在函数或类内部）以 dotted qualname 索引，例如
    ``lazy.__proxy__``、``lazy.__proxy__.__init__``、``Foo.bar.helper``
  - ClassDef 自身也作为条目（取 class docstring 作 purpose）
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
        _collect_functions(tree, rel_path, imports, functions, calls_map, parent_qual="")

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
        _collect_ranges(tree, ranges, parent_qual="")
        return ranges

    def get_function_source(self, file_path: Path, qualname: str) -> Optional[str]:
        """精确提取指定函数/类/方法的完整源码（支持任意深度 dotted qualname）。"""
        source = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None

        node = _find_function_node(tree, qualname)
        if node is None:
            return None

        lines = source.splitlines()
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
    parent_qual: str,
) -> None:
    """递归收集所有 class/function 定义，使用 dotted qualname。

    嵌套规则：
      - 顶层 ``def foo``：``foo``
      - 类方法 ``class Foo: def bar``：``Foo.bar``
      - 嵌套类 ``def lazy(): class __proxy__``：``lazy.__proxy__``
      - 嵌套类方法：``lazy.__proxy__.__init__``
      - ClassDef 自身也作为条目（purpose = class docstring）
    """
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            qual = f"{parent_qual}.{node.name}" if parent_qual else node.name
            docstring = ast.get_docstring(node) or ""
            purpose = docstring.split("\n")[0].strip() if docstring else ""
            functions[qual] = FunctionData(purpose=purpose)
            # class 自身不收 calls（其 body 由内部方法各自记录）
            calls_map.setdefault(qual, [])
            _collect_functions(node, module_path, imports, functions, calls_map, qual)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{parent_qual}.{node.name}" if parent_qual else node.name
            docstring = ast.get_docstring(node) or ""
            purpose = docstring.split("\n")[0].strip() if docstring else ""
            functions[qual] = FunctionData(purpose=purpose)
            calls_map[qual] = _extract_calls(node, imports, module_path)
            # 递归找函数体内的嵌套 class / 嵌套函数（_extract_calls 会跳过这些）
            _collect_functions(node, module_path, imports, functions, calls_map, qual)


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


def _find_function_node(tree: ast.AST, qualname: str) -> Optional[ast.AST]:
    """根据 dotted qualname 在 AST 中精确定位（支持任意深度嵌套）。

    例如 ``lazy.__proxy__.__init__`` 会按 ``lazy`` → ``__proxy__`` → ``__init__``
    逐级下钻，每层匹配 FunctionDef / AsyncFunctionDef / ClassDef 子节点。
    """
    parts = qualname.split(".") if qualname else []
    if not parts:
        return None
    return _descend(tree, parts)


def _descend(node: ast.AST, parts: list[str]) -> Optional[ast.AST]:
    target, rest = parts[0], parts[1:]
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if child.name == target:
                if not rest:
                    return child
                hit = _descend(child, rest)
                if hit is not None:
                    return hit
    return None


def _collect_ranges(tree: ast.AST, ranges: dict, parent_qual: str) -> None:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{parent_qual}.{node.name}" if parent_qual else node.name
            ranges[qual] = (node.lineno, getattr(node, "end_lineno", node.lineno))
            _collect_ranges(node, ranges, qual)


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
