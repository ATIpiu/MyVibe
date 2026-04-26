"""代码索引工具：read_file / rebuild_index / find_symbol。

本质是对项目代码文件的结构化读取，归属于文件工具层。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from ..base_tool import BaseTool, ToolRegistry, ToolResult

_manager = None


def set_index_manager(manager) -> None:
    """由 CodingAgent 在初始化时注入全局代码索引管理器。"""
    global _manager
    _manager = manager


def get_injected_manager():
    if _manager is None:
        raise RuntimeError("代码索引管理器未初始化，请先调用 set_index_manager()")
    return _manager


@ToolRegistry.register
class ReadFileTool(BaseTool):
    """智能分层读取项目代码索引（全局文件总览 / 文件函数列表 / 函数源码+调用关系）。"""

    name = "read_file"
    description = (
        "读取项目代码索引。scope 控制读取粒度：\n"
        "- scope='overview'：以紧凑树形文本返回全项目文件总览（只有文件名+模块描述，最省 token）\n"
        "- scope='file'：返回指定文件的函数列表（需提供 files 参数）\n"
        "- scope='function'：返回指定函数的完整源码 + 调用关系（需提供 function_key 参数）\n"
        "function_key 格式：'module_path:qualname'，类方法写法：'src/foo.py:MyClass.method'\n\n"
        "## 标准三层调用链\n"
        "read_file(overview) → 确定目标文件\n"
        "read_file(file, files=[...]) → 确认函数列表\n"
        "read_file(function, function_key=...) → 获取源码\n\n"
        "## 使用优先级：最高\n"
        "在读取任何文件之前，先调用此工具了解项目结构。\n"
        "已知变量名/调用名时，直接用 find_symbol 跳过前两步。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["overview", "file", "function"],
                "description": "读取粒度：overview=全项目文件总览 / file=指定文件函数列表 / function=指定函数源码",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "scope='file' 时必填，文件路径列表，如 ['src/utils/path.py']",
            },
            "function_key": {
                "type": "string",
                "description": "scope='function' 时必填，格式 'module_path:qualname'",
            },
        },
        "required": ["scope"],
    }

    def execute(
        self,
        scope: str,
        files: Optional[list] = None,
        modules: Optional[list] = None,
        function_key: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        mgr = get_injected_manager()
        scope = {"all": "overview", "modules": "file"}.get(scope, scope)

        # 容错：模型常把 files (复数+数组) 写成 file / module / module_path 等
        # 单数形式，且值常为单个字符串。统一归一化到 effective_files: list[str]。
        alias_value = (
            files
            or modules
            or kwargs.get("file")
            or kwargs.get("module")
            or kwargs.get("module_path")
            or kwargs.get("file_path")
            or kwargs.get("paths")
        )
        if isinstance(alias_value, str):
            alias_value = [alias_value]
        effective_files = alias_value or None

        if scope == "overview":
            return self._read_overview(mgr)
        elif scope == "file":
            if not effective_files:
                return ToolResult(content="scope='file' 时必须提供 files 参数（list[str]）", is_error=True)
            return self._read_files(mgr, effective_files)
        elif scope == "function":
            if not function_key:
                return ToolResult(content="scope='function' 时必须提供 function_key 参数", is_error=True)
            return self._read_function(mgr, function_key)
        else:
            return ToolResult(content=f"不支持的 scope: {scope}", is_error=True)

    def _read_overview(self, mgr) -> ToolResult:
        overview_text = mgr.render_overview()
        if not overview_text:
            return ToolResult(content="索引为空，请先执行 rebuild_index 建立索引")
        total = len(mgr.read_all())
        return ToolResult(content=f"项目文件总览（{total} 个模块）\n\n{overview_text}")

    def _read_files(self, mgr, file_paths: list) -> ToolResult:
        lines = []
        missing = []
        for module_path in file_paths:
            module_data = mgr.read_module(module_path)
            if module_data is None:
                missing.append(module_path)
                continue
            func_count = len(module_data.functions)
            lines.append(f"## {module_path}（{func_count} 个条目）")
            if module_data.purpose:
                lines.append(f"模块描述：{module_data.purpose}")
            lines.append("")

            # 按 class 归组显示（按最后一个 . 拆 class.method）：嵌套 class 也用
            # 整段前缀作为 group key，例如 ``lazy.__proxy__.__init__`` →
            # group=``lazy.__proxy__``、leaf=``__init__``。这样深嵌套不会平铺成一坨。
            top_funcs: list[tuple[str, str]] = []
            groups: dict[str, list[tuple[str, str]]] = {}
            for qualname, func_data in module_data.functions.items():
                purpose = func_data.purpose or "（无描述）"
                if "." in qualname:
                    group, leaf = qualname.rsplit(".", 1)
                    groups.setdefault(group, []).append((leaf, purpose))
                else:
                    top_funcs.append((qualname, purpose))

            for q, p in top_funcs:
                lines.append(f"  {q}: {p}")
            for group in sorted(groups.keys()):
                lines.append(f"  {group}/")
                for leaf, p in groups[group]:
                    lines.append(f"    {leaf}: {p}")
            lines.append("")

        if missing:
            lines.append(f"未找到的模块（可能未同步）：{', '.join(missing)}")

        if not lines:
            return ToolResult(content="未找到任何指定模块，请检查路径或执行 rebuild_index")
        return ToolResult(content="\n".join(lines))

    def _read_function(self, mgr, function_key: str) -> ToolResult:
        # 容错：模型常把缺失的 qualname 序列化为 "None" / "null" / 空串。
        # 此时直接退化为 scope='file'，把该文件的函数列表喂回去，模型挑一个再调即可。
        parts = (function_key or "").split(":", 1)
        if len(parts) == 2:
            module_path, qual = parts
            if qual.strip().lower() in ("", "none", "null", "undefined"):
                if mgr.read_module(module_path) is not None:
                    listing = self._read_files(mgr, [module_path])
                    return ToolResult(
                        content=(
                            f"function_key 缺少 qualname（你传的是 ':{qual}'）。"
                            f"已自动列出 {module_path} 的所有函数 / 类，"
                            "请挑选一个 qualname 后用 "
                            "`read_file(scope='function', function_key='"
                            f"{module_path}:<qualname>')` 重试：\n\n"
                            + (listing.content or "")
                        )
                    )

        source = mgr.read_function_source(function_key)
        if source is None:
            # 模糊回退：用最后一段做后缀匹配，给出候选 qualname 让模型重试
            parts = function_key.split(":", 1)
            if len(parts) == 2:
                module_path, qual = parts
                last = qual.rsplit(".", 1)[-1]
                candidates = mgr.find_qualname_candidates(module_path, last)
                if candidates:
                    hint_lines = "\n".join(
                        f"  - {module_path}:{c}" for c in candidates
                    )
                    return ToolResult(
                        content=(
                            f"未找到精确匹配 '{function_key}'。可能你想找的是：\n"
                            f"{hint_lines}\n\n"
                            "请用上面任一 function_key 重试。"
                        ),
                        is_error=True,
                    )
            return ToolResult(
                content=(
                    f"未找到函数 '{function_key}'，请检查 key 格式（module_path:qualname）"
                    "或执行 rebuild_index"
                ),
                is_error=True,
            )

        callers = mgr.get_callers(function_key)
        callees = mgr.get_callees(function_key)

        lines = [f"# 函数源码：{function_key}", "", "```python", source, "```", ""]

        if callers:
            lines.append(f"**被调用（callers，共 {len(callers)} 个）：**")
            for c in callers[:10]:
                lines.append(f"  - {c}")
            if len(callers) > 10:
                lines.append(f"  ... 共 {len(callers)} 个")
            lines.append("")

        if callees:
            lines.append(f"**调用了（callees，共 {len(callees)} 个）：**")
            for c in callees[:10]:
                lines.append(f"  - {c}")
            if len(callees) > 10:
                lines.append(f"  ... 共 {len(callees)} 个")
            lines.append("")

        if not callers and not callees:
            lines.append("（无已知调用关系）")

        return ToolResult(content="\n".join(lines))


@ToolRegistry.register
class RebuildIndexTool(BaseTool):
    """初始化或重建项目代码文件索引。"""

    name = "rebuild_index"
    description = (
        "初始化或重建项目代码文件索引。\n"
        "- 不填 file_path：扫描整个项目，全量重建\n"
        "- 填写 file_path：只同步单个文件（适合刚修改某个文件后快速更新）\n"
        "注意：write_file / edit_file 会自动触发单文件同步，通常无需手动调用。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "可选，指定文件路径则只同步该文件；不填则全项目扫描",
            },
        },
    }

    def execute(self, file_path: Optional[str] = None) -> ToolResult:
        mgr = get_injected_manager()
        result = mgr.sync(file_path)

        if file_path:
            if result.get("module_deleted"):
                return ToolResult(content=f"已删除模块索引：{result['module_deleted']}")
            module = result.get("module", file_path)
            count = result.get("functions_count", 0)
            return ToolResult(content=f"已同步：{module}（{count} 个函数）")
        else:
            files = result.get("files_processed", 0)
            funcs = result.get("total_functions", 0)
            modules = result.get("total_modules", 0)
            return ToolResult(
                content=f"全项目同步完成：{files} 个文件 / {modules} 个模块 / {funcs} 个函数"
            )


@ToolRegistry.register
class FindSymbolTool(BaseTool):
    name = "find_symbol"
    description = (
        "查找符号（变量名、函数调用、类名）出现在哪些函数中，返回 module_path:qualname 列表。\n"
        "返回结果可直接传给 read_file(scope='function')，无需读整个文件。\n\n"
        "典型场景：不知道某变量/调用在哪个函数里 → find_symbol → read_file(function)"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "要查找的符号名，支持正则"},
            "path":   {"type": "string", "description": "搜索范围目录，默认项目根目录"},
            "glob":   {"type": "string", "description": "文件过滤，默认 '*.py'"},
        },
        "required": ["symbol"],
    }

    def execute(self, symbol: str, path: Optional[str] = None, glob: str = "*.py") -> ToolResult:
        mgr = get_injected_manager()
        file_line_map = self._grep_symbol(symbol, path or str(mgr.project_root), glob)
        if not file_line_map:
            return ToolResult(content=f"未找到符号 '{symbol}'")

        found, seen = [], set()
        for abs_path, line_numbers in file_line_map.items():
            try:
                rel_path = str(Path(abs_path).relative_to(mgr.project_root)).replace("\\", "/")
            except ValueError:
                rel_path = abs_path
            ranges = mgr.get_function_ranges(rel_path)
            for ln in line_numbers:
                func_key = _find_enclosing_function(ranges, ln) if ranges else None
                key = f"{rel_path}:{func_key}" if func_key else f"{rel_path}:(module-level)"
                if key not in seen:
                    seen.add(key)
                    found.append(key)

        lines = [f'找到 "{symbol}" 出现在 {len(found)} 个位置：']
        lines += [f"  {k}" for k in found]
        lines.append("\n提示：用 read_file(scope='function', function_key='...') 查看源码")
        return ToolResult(content="\n".join(lines))

    def _grep_symbol(self, symbol: str, base: str, glob_filter: str) -> dict[str, list[int]]:
        file_line_map: dict[str, list[int]] = {}
        try:
            proc = subprocess.run(
                ["rg", "--no-heading", "-n", "--glob", glob_filter, symbol, base],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if proc.returncode in (0, 1):
                for line in proc.stdout.splitlines():
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        try:
                            file_line_map.setdefault(parts[0], []).append(int(parts[1]))
                        except ValueError:
                            pass
                return file_line_map
        except Exception:
            pass
        try:
            regex = re.compile(symbol)
            for fp in Path(base).rglob(glob_filter):
                if not fp.is_file():
                    continue
                try:
                    hits = [
                        i + 1
                        for i, ln in enumerate(
                            fp.read_text(encoding="utf-8", errors="replace").splitlines()
                        )
                        if regex.search(ln)
                    ]
                    if hits:
                        file_line_map[str(fp)] = hits
                except Exception:
                    pass
        except re.error:
            pass
        return file_line_map


def _find_enclosing_function(
    ranges: dict[str, tuple[int, int]], line_no: int
) -> Optional[str]:
    best, best_size = None, float("inf")
    for qualname, (start, end) in ranges.items():
        if start <= line_no <= end and (end - start) < best_size:
            best, best_size = qualname, end - start
    return best
