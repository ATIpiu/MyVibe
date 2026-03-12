"""子代理：上下文隔离的独立 Agent，用于处理子任务。

SubAgent 拥有独立的 messages 列表，不会污染父 Agent 的对话历史。
通过直接调用 llm._stream_chat_impl()（而非 stream_chat/chat），
绕过主历史收集，同时仍通过 _accumulate_session() 计入 token 统计。

使用场景：
  - 并行/独立处理子任务（重构单个文件、搜索信息）
  - 避免子任务噪音污染主对话上下文
  - 父 Agent 需要等待子任务结果时（同步）

递归限制：spawn_agent 工具会自动从子 Agent 的工具列表中排除，
防止子 Agent 再次 spawn_agent，避免无限递归。
"""
from __future__ import annotations

import time
from typing import Optional

from ..llm.base_client import LLMClient
from ..tools.base_tool import ToolRegistry, ToolResult

_SUB_AGENT_SYSTEM = (
    "你是一个专注完成特定子任务的助手代理。"
    "请聚焦于被分配的任务，完成后用清晰的文字总结你做了什么以及结果是什么。"
    "不要发散到无关的话题。"
)

_MAX_ITERATIONS = 10


class SubAgent:
    """上下文隔离的子代理。

    Args:
        llm: 与父 Agent 共享的 LLM 客户端（token 统计共用）
        tools_schema: 可用工具列表（会自动排除 spawn_agent）
        system: 可选的自定义系统提示词
    """

    def __init__(
        self,
        llm: LLMClient,
        tools_schema: list[dict],
        system: Optional[str] = None,
    ) -> None:
        self.llm = llm
        # 排除 spawn_agent，防止无限递归
        self.tools_schema = [t for t in tools_schema if t.get("name") != "spawn_agent"]
        self.system = system or _SUB_AGENT_SYSTEM
        self.messages: list[dict] = []

    def run(self, task: str, context: str = "") -> str:
        """执行子任务，返回最终文字结果。

        Args:
            task: 任务描述
            context: 额外上下文信息（可选）
        """
        user_content = f"背景信息：{context}\n\n任务：{task}" if context else task
        self.messages.append({"role": "user", "content": user_content})

        for _ in range(_MAX_ITERATIONS):
            # 直接调用底层实现，绕过主历史收集
            response = self.llm._stream_chat_impl(
                messages=self.messages,
                system=self.system,
                tools=self.tools_schema,
            )
            # 仍计入 session token 统计
            self.llm._accumulate_session(response)

            # 构建 assistant content blocks
            content_blocks: list[dict] = []
            if response.text_content:
                content_blocks.append({"type": "text", "text": response.text_content})
            for tc in response.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.tool_use_id,
                    "name": tc.name,
                    "input": tc.input,
                })

            if content_blocks:
                self.messages.append({"role": "assistant", "content": content_blocks})

            # 没有工具调用：任务完成
            if not response.tool_calls:
                return response.text_content or ""

            # 执行工具调用
            tool_results: list[dict] = []
            for tc in response.tool_calls:
                result_dict = self._execute_tool(tc.name, tc.input, tc.tool_use_id)
                tool_results.append(result_dict)

            self.messages.append({"role": "user", "content": tool_results})

        return f"子代理达到最大迭代次数（{_MAX_ITERATIONS}），任务可能未完成。"

    def _execute_tool(self, tool_name: str, tool_input: dict, tool_use_id: str) -> dict:
        """执行单个工具调用，返回 tool_result dict。"""
        try:
            tool = ToolRegistry.instantiate(tool_name)
            result: ToolResult = tool.execute(**tool_input)
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result.content or "",
                "is_error": result.is_error,
            }
        except KeyError:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"工具 '{tool_name}' 未注册",
                "is_error": True,
            }
        except Exception as e:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"工具执行异常: {e}",
                "is_error": True,
            }
