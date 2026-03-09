"""Agent 抽象基类：定义 run_turn / handle_tool_calls 接口契约。"""
from __future__ import annotations

import abc
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.base_client import HistoryEntry, LLMClient
    from .state import AgentState


class BaseAgent(abc.ABC):
    """所有 Agent 的抽象基类。

    子类需要实现：
        run_turn()         — 处理一轮用户输入，执行 agentic 循环
        handle_tool_calls() — 并行执行工具调用

    基类提供：
        get_conversation_history() — 返回完整对话历史（LLM 输入+输出）
        get_messages()             — 返回当前 AgentState 中的消息列表
    """

    # 子类初始化时应设置这两个属性
    llm: "LLMClient"
    state: "AgentState"

    # ── 子类必须实现 ──────────────────────────────────────

    @abc.abstractmethod
    def run_turn(self, user_input: str) -> str:
        """处理一轮用户输入，执行 agentic 循环，返回最终文本响应。

        Args:
            user_input: 用户输入的文本

        Returns:
            Agent 的最终文本响应
        """
        ...

    @abc.abstractmethod
    def handle_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        """并行执行工具调用，返回 tool_result 列表。

        Args:
            tool_calls: LLM 请求的工具调用列表，每项包含 name/id/input

        Returns:
            Anthropic tool_result 消息内容列表
        """
        ...

    # ── 历史查询（基类提供默认实现） ─────────────────────

    def get_conversation_history(self) -> list["HistoryEntry"]:
        """返回 LLM 客户端收集的完整对话历史（输入消息 + LLM 响应交替）。

        历史由 LLMClient 自动收集，包含：
          - 每次调用时新增的 user / assistant / tool_result 消息
          - 每次 LLM 响应（文本 + 工具调用 + token 用量）
        """
        if hasattr(self, "llm") and self.llm is not None:
            return self.llm.get_history()
        return []

    def get_messages(self) -> list[dict]:
        """返回当前会话的 Anthropic 格式消息列表（来自 AgentState）。"""
        if hasattr(self, "state") and self.state is not None:
            return list(self.state.messages)
        return []

    def get_turn_count(self) -> int:
        """返回当前会话的对话轮数。"""
        if hasattr(self, "state") and self.state is not None:
            return self.state.turn
        return 0
