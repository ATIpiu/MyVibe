"""询问用户工具：阻塞等待用户回答，支持选项选择和自由文本输入。

用法：
    1. 在启动时调用 set_ask_user_handler(fn) 注入 UI 实现
    2. LLM 调用 ask_user 工具时，自动调用已注入的 handler
"""
from __future__ import annotations

from typing import Callable, Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult

# 由 main.py 在 prompt_session 创建后注入
# 签名：(question: str, options: list[str] | None, allow_custom: bool) -> str | None
_ask_user_handler: Optional[Callable] = None


def set_ask_user_handler(fn: Callable) -> None:
    """注入 UI 实现。fn(question, options, allow_custom) -> str | None。"""
    global _ask_user_handler
    _ask_user_handler = fn


@ToolRegistry.register
class AskUserTool(BaseTool):
    """向用户提问，阻塞等待回答后继续。"""

    name = "ask_user"
    description = (
        "向用户提问，等待用户回答后继续。\n\n"
        "## 何时使用\n"
        "- 任务存在歧义，需要用户澄清\n"
        "- 有多种实现方案，需用户选择偏好\n"
        "- 执行不可逆操作前需确认\n\n"
        "## 参数说明\n"
        "- question：问题描述\n"
        "- options：可选选项列表（提供时显示选择菜单）\n"
        "- allow_custom：是否允许用户输入自定义内容（默认 true）\n\n"
        "## 使用原则\n"
        "每次只问一个问题。问题要具体，不要模糊。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要向用户提出的问题",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选选项列表（省略则纯文本输入）",
            },
            "allow_custom": {
                "type": "boolean",
                "description": "是否允许自定义输入（默认 true）",
                "default": True,
            },
        },
        "required": ["question"],
    }

    def execute(
        self,
        question: str,
        options: Optional[list[str]] = None,
        allow_custom: bool = True,
    ) -> ToolResult:
        if _ask_user_handler is None:
            # 降级：无 UI 环境，返回错误提示
            return ToolResult(
                content="ask_user 未初始化（无交互环境），请在描述中直接说明问题",
                is_error=True,
            )

        try:
            answer = _ask_user_handler(question, options or [], allow_custom)
            if answer is None:
                return ToolResult(content="用户取消了回答", is_error=False)
            return ToolResult(content=f"用户回答：{answer}")
        except Exception as e:
            return ToolResult(content=f"ask_user 执行失败: {e}", is_error=True)
