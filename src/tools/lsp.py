"""LSP 工具：Phase1 为 stub，Phase2 接入 pylsp JSON-RPC。"""
from __future__ import annotations

from typing import Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult


class LspClient:
    """JSON-RPC LSP 客户端桥接（Phase2 实现）。"""

    def __init__(self, server_cmd: str = "pylsp"):
        self.server_cmd = server_cmd
        self._initialized = False

    def initialize(self, root_path: str) -> bool:
        """发送 LSP initialize 请求（Phase2 实现）。"""
        # TODO: Phase2 - 启动 LSP 服务器并发送 initialize
        self._initialized = False
        return False

    def hover(self, file_path: str, line: int, character: int) -> Optional[str]:
        """获取指定位置的 hover 信息（Phase2 实现）。"""
        # TODO: Phase2 - textDocument/hover JSON-RPC 调用
        return None

    def goto_definition(self, file_path: str, line: int, character: int) -> Optional[dict]:
        """跳转到定义（Phase2 实现）。"""
        # TODO: Phase2 - textDocument/definition JSON-RPC 调用
        return None


@ToolRegistry.register
class LspHoverTool(BaseTool):
    """获取代码符号的类型信息和文档（LSP hover）。Phase1 为 stub。"""

    name = "lsp_hover"
    description = (
        "获取指定文件位置的符号类型信息和文档（通过 LSP）。"
        "Phase1 为占位实现，Phase2 接入 pylsp。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "文件路径",
            },
            "line": {
                "type": "integer",
                "description": "行号（从1开始）",
            },
            "character": {
                "type": "integer",
                "description": "列号（从0开始）",
            },
        },
        "required": ["file_path", "line", "character"],
    }

    def execute(self, file_path: str, line: int, character: int) -> ToolResult:
        return ToolResult(
            content="[LSP Phase1 stub] LSP hover 功能将在 Phase2 实现。"
                    f"查询位置: {file_path}:{line}:{character}",
            is_error=False,
        )
