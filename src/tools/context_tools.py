"""上下文管理工具（内部模块，不再向 LLM 暴露工具）。

原有 get_file_summary / get_function_code / index_project / search_functions
已被 read_memory / rebuild_memory 替代，此处移除工具注册。
set_context_manager / get_context_manager 保留供 coding_agent 内部使用。
"""
from __future__ import annotations

from typing import Optional

from .base_tool import BaseTool, ToolResult
from ..context.context_manager import ContextManager
from ..context.file_summary import format_summary

# 模块级 ContextManager 实例（由 CodingAgent 初始化后注入）
_context_manager: Optional[ContextManager] = None


def set_context_manager(cm: ContextManager) -> None:
    """由 CodingAgent 在初始化时注入 ContextManager 实例。"""
    global _context_manager
    _context_manager = cm


def get_context_manager() -> Optional[ContextManager]:
    return _context_manager


class GetFileSummaryTool(BaseTool):
    """探索阶段：获取文件的函数摘要列表（不读取完整代码）。"""

    name = "get_file_summary"
    description = (
        "获取文件的函数/方法摘要列表（签名+描述+示例）。"
        "探索阶段使用，无需读取完整文件内容。"
        "需要修改某个函数时，再用 get_function_code 精确读取。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要分析的文件路径（绝对或相对）",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str) -> ToolResult:
        cm = get_context_manager()
        if cm is None:
            return ToolResult(content="ContextManager 未初始化", is_error=True)
        summary = cm.get_file_summary(file_path)
        if summary is None:
            return ToolResult(content=f"无法解析文件: {file_path}", is_error=True)
        return ToolResult(
            content=format_summary(summary),
            metadata={"function_count": len(summary.functions)},
        )


class GetFunctionCodeTool(BaseTool):
    """精确提取单个函数的完整源码（编辑前使用）。"""

    name = "get_function_code"
    description = (
        "精确提取指定函数的完整源码。"
        "需要理解或修改某个函数时使用，比 read_file 更高效。"
        "修改前建议先用 get_file_summary 了解文件结构。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "文件路径",
            },
            "func_name": {
                "type": "string",
                "description": "函数名",
            },
            "class_name": {
                "type": "string",
                "description": "所属类名（如果是方法）",
            },
        },
        "required": ["file_path", "func_name"],
    }

    def execute(
        self,
        file_path: str,
        func_name: str,
        class_name: Optional[str] = None,
    ) -> ToolResult:
        cm = get_context_manager()
        if cm is None:
            return ToolResult(content="ContextManager 未初始化", is_error=True)
        code = cm.get_function_code(file_path, func_name, class_name)
        if not code or code.startswith("未找到函数"):
            return ToolResult(content=code or f"未找到函数 '{func_name}'", is_error=True)
        return ToolResult(content=code)


class IndexProjectTool(BaseTool):
    """项目启动：批量扫描并建立全项目函数索引。"""

    name = "index_project"
    description = (
        "扫描项目所有源码文件，建立函数索引。"
        "项目启动时调用一次，后续 search_functions 依赖此索引。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "glob_pattern": {
                "type": "string",
                "description": "文件匹配模式，默认 '**/*.py'",
                "default": "**/*.py",
            },
        },
    }

    def execute(self, glob_pattern: str = "**/*.py") -> ToolResult:
        cm = get_context_manager()
        if cm is None:
            return ToolResult(content="ContextManager 未初始化", is_error=True)
        index = cm.index_project(glob_pattern)
        total_files = len(index)
        total_funcs = sum(len(s.functions) for s in index.values())
        lines = [f"项目索引完成: {total_files} 个文件，{total_funcs} 个函数\n"]
        for fp, summary in sorted(index.items()):
            rel = fp.replace(str(cm.project_root), "").lstrip("/\\")
            lines.append(f"  {rel}: {len(summary.functions)} 个函数")
        return ToolResult(content="\n".join(lines[:50]))  # 截断显示


class SearchFunctionsTool(BaseTool):
    """跨文件函数搜索：按名称或描述模糊查找。"""

    name = "search_functions"
    description = (
        "在项目索引中按函数名称或描述搜索。"
        "用于跨文件快速定位功能实现，无需逐文件 read_file。"
        "需要先执行 index_project 建立索引。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（函数名或功能描述）",
            },
            "top_k": {
                "type": "integer",
                "description": "最多返回结果数，默认10",
                "default": 10,
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, top_k: int = 10) -> ToolResult:
        cm = get_context_manager()
        if cm is None:
            return ToolResult(content="ContextManager 未初始化", is_error=True)
        results = cm.search_functions(query, top_k)
        if not results:
            return ToolResult(content=f"未找到与 '{query}' 相关的函数")

        lines = [f"搜索 '{query}' 找到 {len(results)} 个结果:\n"]
        for file_path, func in results:
            rel = file_path.replace(str(cm.project_root), "").lstrip("/\\")
            class_prefix = f"{func.class_name}." if func.class_name else ""
            desc = f" - {func.description}" if func.description else ""
            lines.append(f"  {rel} :: {class_prefix}{func.name}{desc}")

        return ToolResult(content="\n".join(lines))
