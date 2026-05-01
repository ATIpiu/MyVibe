"""Anthropic 原生客户端：支持 Prompt Cache、工具缓存、原生流式解析。

与 OpenAIClient 的核心区别：
  1. 直接使用 anthropic Python SDK，无需格式转换（MyVibe 内部已是 Anthropic 格式）
  2. build_cached_system() 返回 list[dict]，静态段加 cache_control
  3. 工具列表末尾加 cache_control，缓存整个工具定义
  4. usage 额外解析 cache_read_input_tokens / cache_creation_input_tokens

配置示例（config.yaml）：
  llm:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: sk-ant-...
    max_tokens: 8192

Prompt Cache 节省原理：
  - 静态 system prompt 约 800 tokens，每轮缓存命中节省 90% 费用
  - 工具定义约 2000+ tokens，缓存后每轮只计 0.1x 费用
  - cache_creation 时费用 1.25x，命中时费用 0.1x（5 分钟 TTL）
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional, Union

from .base_client import LLMClient, LLMResponse, ToolCall


_MAX_RETRIES = 6
_RETRY_DELAYS = [1, 2, 4, 8, 16, 32]

# Anthropic 各模型 cache 最小 token 要求（低于此值不缓存，不报错）
_MIN_CACHE_TOKENS = {
    "claude-opus-4-7": 4096,
    "claude-sonnet-4-6": 2048,
    "claude-haiku-4-5": 4096,
    "claude-haiku-3-5": 2048,
}
_DEFAULT_MIN_CACHE_TOKENS = 1024


class AnthropicClient(LLMClient):
    """Anthropic 原生客户端，支持 Prompt Cache 静态/动态分区。"""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 1.0,
        base_url: str = "https://api.anthropic.com",
        history_file: Optional[str] = None,
        extra_body: Optional[dict] = None,
    ) -> None:
        super().__init__(history_file=history_file)

        if not api_key:
            raise ValueError(
                "api_key 不能为空。请在 config.yaml 填写 api_key，"
                "或设置环境变量 ANTHROPIC_API_KEY。"
            )
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.extra_body = extra_body or {}

        # 验证 anthropic 包已安装
        try:
            import anthropic as _anthropic
            self._anthropic = _anthropic
        except ImportError:
            raise ImportError(
                "使用 Anthropic provider 需要安装 anthropic 包：\n"
                "  pip install anthropic"
            )

    def build_cached_system(self, static: str, dynamic: str) -> list[dict]:
        """返回带 cache_control 的 system blocks 列表。

        静态段（规则/能力）加 cache_control，在 5 分钟 TTL 内命中缓存只收 0.1x 费用。
        动态段（环境/记忆上下文）不加缓存标记。
        """
        blocks: list[dict] = []
        if static.strip():
            blocks.append({
                "type": "text",
                "text": static,
                "cache_control": {"type": "ephemeral"},
            })
        if dynamic.strip():
            blocks.append({
                "type": "text",
                "text": dynamic,
            })
        return blocks

    def _build_client(self):
        """创建 anthropic.Anthropic 实例。"""
        kwargs = {"api_key": self.api_key}
        if self.base_url and self.base_url != "https://api.anthropic.com":
            kwargs["base_url"] = self.base_url
        return self._anthropic.Anthropic(**kwargs)

    def _add_tools_cache(self, tools: list[dict]) -> list[dict]:
        """在工具列表最后一项加 cache_control，缓存整个工具定义块。"""
        if not tools:
            return tools
        tools_copy = list(tools)
        last = dict(tools_copy[-1])
        last["cache_control"] = {"type": "ephemeral"}
        tools_copy[-1] = last
        return tools_copy

    def _stream_chat_impl(
        self,
        messages: list[dict],
        system: Optional[Union[str, list[dict]]] = None,
        tools: Optional[list[dict]] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> LLMResponse:
        client = self._build_client()
        start_time = time.monotonic()

        # system：接受字符串或已分好的 blocks 列表
        if isinstance(system, list):
            system_blocks = system
        elif system:
            system_blocks = [{"type": "text", "text": system}]
        else:
            system_blocks = None

        # 工具：末尾加缓存标记
        tools_with_cache = self._add_tools_cache(tools) if tools else []

        params: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system_blocks:
            params["system"] = system_blocks
        if tools_with_cache:
            params["tools"] = tools_with_cache

        last_exc: Exception = RuntimeError("未知错误")

        for attempt in range(_MAX_RETRIES + 1):
            text_buffer = ""
            tool_buffers: dict[str, dict] = {}
            index_to_id: dict[int, str] = {}
            usage: dict = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
            stop_reason = "end_turn"

            _on_text = on_text if attempt == 0 else None
            _on_tool_start = on_tool_start if attempt == 0 else None

            try:
                with client.messages.stream(**params) as stream:
                    for event in stream:
                        if cancel_event and cancel_event.is_set():
                            break

                        etype = event.type

                        if etype == "message_start":
                            u = event.message.usage
                            usage["input_tokens"] = u.input_tokens
                            usage["cache_read_input_tokens"] = getattr(u, "cache_read_input_tokens", 0) or 0
                            usage["cache_creation_input_tokens"] = getattr(u, "cache_creation_input_tokens", 0) or 0

                        elif etype == "content_block_start":
                            cb = event.content_block
                            idx = event.index
                            if cb.type == "tool_use":
                                tool_id = cb.id
                                index_to_id[idx] = tool_id
                                tool_buffers[tool_id] = {"name": cb.name, "input_json": ""}
                                if _on_tool_start:
                                    _on_tool_start(cb.name, tool_id)

                        elif etype == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                text_buffer += delta.text
                                if _on_text:
                                    _on_text(delta.text)
                            elif delta.type == "input_json_delta":
                                tool_id = index_to_id.get(event.index)
                                if tool_id:
                                    tool_buffers[tool_id]["input_json"] += delta.partial_json

                        elif etype == "message_delta":
                            stop_reason = getattr(event.delta, "stop_reason", stop_reason) or stop_reason
                            if hasattr(event, "usage"):
                                usage["output_tokens"] = event.usage.output_tokens

                # 成功，从最终消息获取完整 usage
                final_msg = stream.get_final_message()
                fu = final_msg.usage
                usage["input_tokens"] = fu.input_tokens
                usage["output_tokens"] = fu.output_tokens
                usage["cache_read_input_tokens"] = getattr(fu, "cache_read_input_tokens", 0) or 0
                usage["cache_creation_input_tokens"] = getattr(fu, "cache_creation_input_tokens", 0) or 0
                break

            except self._anthropic.APIStatusError as e:
                raise RuntimeError(f"Anthropic API 错误 [{e.status_code}]: {e.message}") from e
            except Exception as e:
                last_exc = e
                if isinstance(e, RuntimeError):
                    raise
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(
                        f"\n[重试 {attempt + 1}/{_MAX_RETRIES}] 网络中断（{type(e).__name__}），"
                        f"{delay}s 后重试...",
                        flush=True,
                    )
                    time.sleep(delay)
                    _on_text = on_text
                    _on_tool_start = on_tool_start
                else:
                    raise RuntimeError(
                        f"Anthropic API 请求失败（已重试 {_MAX_RETRIES} 次）: {last_exc}"
                    ) from last_exc

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # 解析工具调用
        tool_calls: list[ToolCall] = []
        for tid, tdata in tool_buffers.items():
            try:
                args = json.loads(tdata["input_json"]) if tdata["input_json"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(name=tdata["name"], tool_use_id=tid, input=args))

        # 打印缓存命中情况（调试信息，仅在有缓存时显示）
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        if cache_read or cache_create:
            print(
                f"\n[cache] 命中 {cache_read:,} tokens，写入 {cache_create:,} tokens",
                flush=True,
            )

        return LLMResponse(
            text_content=text_buffer,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            elapsed_ms=elapsed_ms,
            cost_usd=self._calc_anthropic_cost(usage),
        )

    def _calc_anthropic_cost(self, usage: dict) -> float:
        """按 Anthropic 官方定价计算费用（USD）。缓存读取仅收 0.1x。"""
        # claude-sonnet-4-6 定价（2025年）
        price_map = {
            "claude-opus-4-7":   (15.0, 75.0),   # (input/MTok, output/MTok)
            "claude-sonnet-4-6": (3.0, 15.0),
            "claude-haiku-4-5":  (0.8, 4.0),
        }
        in_price, out_price = price_map.get(self.model, (3.0, 15.0))
        per_token = 1 / 1_000_000

        regular_in = usage.get("input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        out_tokens = usage.get("output_tokens", 0)

        cost = (
            regular_in * in_price * per_token
            + cache_create * in_price * 1.25 * per_token  # 写入缓存 1.25x
            + cache_read * in_price * 0.10 * per_token    # 读缓存 0.1x
            + out_tokens * out_price * per_token
        )
        return round(cost, 6)

    def count_tokens(self, messages: list[dict], system="") -> int:
        system_str = self._system_to_str(system)
        total_chars = len(system_str)
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(
                            block.get("text", "") or block.get("content", "")
                        )
        return total_chars // 4 + 10
