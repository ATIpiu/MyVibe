"""记忆工具：2个智能工具替代原来的14个细碎工具。

read_memory   - 分层读取（all / modules / function）
rebuild_memory - 初始化或重建记忆索引
"""
from __future__ import annotations

from typing import Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult

_manager = None


def set_memory_manager(manager) -> None:
    """由 CodingAgent 在初始化时注入全局记忆管理器。"""
    global _manager
    _manager = manager


def _get_manager():
    if _manager is None:
        raise RuntimeError("记忆管理器未初始化，请先调用 set_memory_manager()")
    return _manager


@ToolRegistry.register
class ReadMemoryTool(BaseTool):
    """智能分层读取项目记忆（函数列表 / 模块详情 / 函数源码+调用关系）。"""

    name = "read_memory"
    description = (
        "读取项目代码记忆。scope 控制读取粒度：\n"
        "- scope='all'：返回全项目所有模块及其函数列表（项目全览）\n"
        "- scope='modules'：返回指定模块的函数列表（需提供 modules 参数）\n"
        "- scope='function'：返回指定函数的完整源码 + 调用关系（需提供 function_key 参数）\n"
        "function_key 格式：'module_path:qualname'，类方法写法：'src/foo.py:MyClass.method'\n\n"
        "## 使用优先级：最高\n"
        "在读取任何文件之前，先调用此工具了解项目结构。\n"
        "scope='all' 获取模块列表 → scope='modules' 获取函数列表 → scope='function' 获取函数源码。\n"
        "多数情况下 scope='function' 已能替代 read_file，节省大量 token。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "modules", "function"],
                "description": "读取粒度：all=全项目 / modules=指定模块 / function=指定函数",
            },
            "modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "scope='modules' 时必填，模块路径列表，如 ['src/utils/path.py']",
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
        modules: Optional[list] = None,
        function_key: Optional[str] = None,
    ) -> ToolResult:
        """智能读取项目记忆，按 scope 返回不同粒度的信息。"""
        mgr = _get_manager()

        if scope == "all":
            return self._read_all(mgr)
        elif scope == "modules":
            if not modules:
                return ToolResult(content="scope='modules' 时必须提供 modules 参数", is_error=True)
            return self._read_modules(mgr, modules)
        elif scope == "function":
            if not function_key:
                return ToolResult(content="scope='function' 时必须提供 function_key 参数", is_error=True)
            return self._read_function(mgr, function_key)
        else:
            return ToolResult(content=f"不支持的 scope: {scope}", is_error=True)

    def _read_all(self, mgr) -> ToolResult:
        all_memory = mgr.read_all()
        if not all_memory:
            return ToolResult(content="记忆为空，请先执行 rebuild_memory 建立索引")

        lines = [f"项目记忆总览：{len(all_memory)} 个模块\n"]
        for module_path, module_data in sorted(all_memory.items()):
            func_count = len(module_data.functions)
            lines.append(f"## {module_path}（{func_count} 个函数）")
            if module_data.purpose:
                lines.append(f"  {module_data.purpose}")
            for qualname, func_data in module_data.functions.items():
                purpose = func_data.purpose or "（无描述）"
                lines.append(f"  - {qualname}: {purpose}")
            lines.append("")

        return ToolResult(content="\n".join(lines))

    def _read_modules(self, mgr, module_paths: list) -> ToolResult:
        lines = []
        missing = []
        for module_path in module_paths:
            module_data = mgr.read_module(module_path)
            if module_data is None:
                missing.append(module_path)
                continue
            func_count = len(module_data.functions)
            lines.append(f"## {module_path}（{func_count} 个函数）")
            if module_data.purpose:
                lines.append(f"模块描述：{module_data.purpose}")
            lines.append("")
            for qualname, func_data in module_data.functions.items():
                purpose = func_data.purpose or "（无描述）"
                lines.append(f"  {qualname}: {purpose}")
            lines.append("")

        if missing:
            lines.append(f"未找到的模块（可能未同步）：{', '.join(missing)}")

        if not lines:
            return ToolResult(content="未找到任何指定模块，请检查路径或执行 rebuild_memory")
        return ToolResult(content="\n".join(lines))

    def _read_function(self, mgr, function_key: str) -> ToolResult:
        source = mgr.read_function_source(function_key)
        if source is None:
            return ToolResult(
                content=f"未找到函数 '{function_key}'，请检查 key 格式（module_path:qualname）或执行 rebuild_memory",
                is_error=True,
            )

        callers = mgr.get_callers(function_key)
        callees = mgr.get_callees(function_key)

        lines = [
            f"# 函数源码：{function_key}",
            "",
            "```python",
            source,
            "```",
            "",
        ]

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
class RebuildMemoryTool(BaseTool):
    """初始化或重建项目代码记忆索引。"""

    name = "rebuild_memory"
    description = (
        "初始化或重建项目代码记忆索引。\n"
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
        """重建记忆索引，file_path 为 None 时全项目扫描。"""
        mgr = _get_manager()
        result = mgr.sync(file_path)

        if file_path:
            if result.get("module_deleted"):
                return ToolResult(content=f"已删除模块记忆：{result['module_deleted']}")
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
