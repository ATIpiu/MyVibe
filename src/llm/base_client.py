"""LLM 客户端抽象基类 + 公共数据类 + 对话历史收集器。

职责划分：
  - 数据类：StreamEvent / ToolCall / LLMResponse / HistoryEntry
  - LLMClient：抽象接口 + 历史收集钩子
  - 子类只需实现 _stream_chat_impl() 和 count_tokens()

历史收集策略：
  每次调用 stream_chat / chat，通过比较 messages 列表长度，
  增量保存「本次新增的输入消息」和「LLM 返回的完整响应」，
  确保工具调用结果、中间轮次消息不会丢失。
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


# ─────────────────────── 公共数据类 ───────────────────────


@dataclass
class StreamEvent:
    """SSE 流式事件单元。"""
    type: str           # "text" | "tool_start" | "tool_delta" | "done"
    content: str = ""
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input_json: str = ""


@dataclass
class ToolCall:
    """流式聚合后的完整工具调用。"""
    name: str
    tool_use_id: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    """统一的 LLM 响应格式，所有后端共用。"""
    text_content: str
    tool_calls: list[ToolCall]
    stop_reason: str = "end_turn"
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    elapsed_ms: int = 0
    cost_usd: float = 0.0


@dataclass
class HistoryEntry:
    """单条历史记录（输入消息或 LLM 响应）。"""
    timestamp: str              # ISO8601
    entry_type: str             # "input_message" | "llm_response"
    role: str = ""              # 输入消息的 role（"user" / "assistant" / "tool"）
    content: object = None      # 原始 content（str 或 list）
    # 以下字段仅 entry_type == "llm_response" 时有效
    text_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp,
            "entry_type": self.entry_type,
        }
        if self.entry_type == "input_message":
            d["role"] = self.role
            d["content"] = self.content
        else:
            d["text_content"] = self.text_content
            d["tool_calls"] = self.tool_calls
            d["stop_reason"] = self.stop_reason
            d["input_tokens"] = self.input_tokens
            d["output_tokens"] = self.output_tokens
            d["cost_usd"] = self.cost_usd
            d["elapsed_ms"] = self.elapsed_ms
        return d


# ─────────────────────── 抽象基类 ───────────────────────


class LLMClient(abc.ABC):
    """LLM 客户端抽象基类。

    调用方（CodingAgent 等）只依赖此接口，不感知底层模型。

    历史收集：
      stream_chat / chat 在调用前后各触发 _record_new_messages / _record_response，
      子类无需关心持久化细节，只需实现核心推理逻辑。

    扩展方式：
      继承此类，实现 _stream_chat_impl() 和 count_tokens()，
      然后在 client.py 的 _PROVIDER_MAP 中注册即可。
    """

    model: str = ""

    def __init__(self, history_file: Optional[str] = None) -> None:
        """
        Args:
            history_file: 对话历史 JSONL 文件路径。None 表示不持久化。
        """
        self._history_file: Optional[Path] = Path(history_file) if history_file else None
        if self._history_file:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)

        # 用于增量检测：上次调用时 messages 列表的长度
        self._last_messages_len: int = 0

        # 用于检测系统提示词是否变化（只在变化时才写入）
        self._last_system_prompt: Optional[str] = None

        # 内存中的全量历史（InputMessage + LLMResponse 交替）
        self.history: list[HistoryEntry] = []

    # ── 历史收集（内部工具） ──────────────────────────────

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _append_entry(self, entry: HistoryEntry) -> None:
        """写入内存列表并追加到 JSONL 文件。"""
        self.history.append(entry)
        if self._history_file:
            try:
                with open(self._history_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            except Exception as exc:
                # 历史写入失败不阻断主流程
                print(f"[LLMClient] 历史写入失败: {exc}")

    def _record_system_prompt(self, system: Optional[str]) -> None:
        """记录系统提示词（仅在内容发生变化时写入，避免每轮重复）。"""
        if not system:
            return
        if system == self._last_system_prompt:
            return
        self._last_system_prompt = system
        entry = HistoryEntry(
            timestamp=self._now_iso(),
            entry_type="input_message",
            role="system",
            content=system,
        )
        self._append_entry(entry)

    def _record_new_messages(self, messages: list[dict]) -> None:
        """增量记录本次调用中新增的输入消息。

        比较 messages 长度与上次调用时的长度，只保存新增部分，
        避免将整个历史列表重复存储。
        """
        new_msgs = messages[self._last_messages_len:]
        for msg in new_msgs:
            role = msg.get("role", "")
            content = msg.get("content")
            entry = HistoryEntry(
                timestamp=self._now_iso(),
                entry_type="input_message",
                role=role,
                content=content,
            )
            self._append_entry(entry)
        self._last_messages_len = len(messages)

    def _record_response(self, response: LLMResponse) -> None:
        """记录 LLM 响应（文本 + 工具调用 + 用量）。"""
        tool_calls_data = [
            {
                "id": tc.tool_use_id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in response.tool_calls
        ]
        entry = HistoryEntry(
            timestamp=self._now_iso(),
            entry_type="llm_response",
            text_content=response.text_content,
            tool_calls=tool_calls_data,
            stop_reason=response.stop_reason,
            input_tokens=response.usage.get("input_tokens", 0),
            output_tokens=response.usage.get("output_tokens", 0),
            cost_usd=response.cost_usd,
            elapsed_ms=response.elapsed_ms,
        )
        self._append_entry(entry)

    # ── 公开接口 ─────────────────────────────────────────

    def stream_chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
    ) -> LLMResponse:
        """流式调用接口。

        自动完成历史收集：调用前记录新增输入，调用后记录响应。
        子类不需要关心历史逻辑，只需实现 _stream_chat_impl。

        Args:
            messages: 完整对话历史（Anthropic 格式）
            system: 系统提示词
            tools: 工具 schema 列表（Anthropic 格式）
            on_text: 每个文本 delta 的实时回调
            on_tool_start: 工具调用出现时的回调 (tool_name, tool_use_id)

        Returns:
            聚合后的完整 LLMResponse
        """
        self._record_system_prompt(system)
        self._record_new_messages(messages)

        response = self._stream_chat_impl(
            messages=messages,
            system=system,
            tools=tools,
            on_text=on_text,
            on_tool_start=on_tool_start,
        )

        self._record_response(response)
        return response

    def chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        """非流式调用（默认复用 stream_chat，不传回调）。

        子类可覆盖以使用专用非流式端点。
        """
        return self.stream_chat(messages, system, tools)

    def get_history(self) -> list[HistoryEntry]:
        """返回内存中的全量历史条目列表。"""
        return list(self.history)

    def clear_history(self) -> None:
        """清空内存历史缓存（不删除文件）。"""
        self.history.clear()
        self._last_messages_len = 0

    # ── 子类必须实现 ─────────────────────────────────────

    @abc.abstractmethod
    def _stream_chat_impl(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
    ) -> LLMResponse:
        """实际流式推理逻辑，由子类提供。"""

    @abc.abstractmethod
    def count_tokens(self, messages: list[dict], system: str = "") -> int:
        """估算 token 数（用于上下文比例监控）。"""

    def calc_cost(self, input_tokens: int, output_tokens: int) -> float:
        """计算本次调用费用（USD）。子类按定价覆盖。"""
        return 0.0
