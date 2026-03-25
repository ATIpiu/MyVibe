"""OpenAI 兼容接口客户端实现。

适用于所有兼容 OpenAI Chat Completions API 的服务（OpenAI、智谱、DeepSeek 等）。
依赖 base_client.LLMClient，支持流式 + 非流式调用。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from .base_client import LLMClient, LLMResponse, ToolCall


# ── 格式转换工具函数 ───────────────────────────────────────────────────────

def _to_openai_messages(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, str):
            result.append(msg)
            continue
        if not isinstance(content, list):
            result.append(msg)
            continue
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
            new_msg: dict = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls:
                new_msg["tool_calls"] = tool_calls
            result.append(new_msg)
        elif role == "user":
            regular_text: list[str] = []
            for block in content:
                btype = block.get("type")
                if btype == "tool_result":
                    result.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    })
                elif btype == "text":
                    regular_text.append(block.get("text", ""))
            if regular_text:
                result.append({"role": "user", "content": "\n".join(regular_text)})
        else:
            result.append(msg)
    return result


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        if "function" in tool:
            result.append(tool)
        else:
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
    return result


def _parse_tool_buffers(tool_buffers: dict[str, dict]) -> list[ToolCall]:
    tool_calls = []
    for tid, tdata in tool_buffers.items():
        try:
            args = json.loads(tdata["input_json"]) if tdata["input_json"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(ToolCall(name=tdata["name"], tool_use_id=tid, input=args))
    return tool_calls


# ── OpenAI 兼容客户端 ───────────────────────────────────────────────────────

class OpenAIClient(LLMClient):
    """OpenAI Chat Completions 兼容客户端。

    通过配置 base_url 和 api_key，可对接任意兼容 OpenAI 协议的服务。
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 1.0,
        base_url: str = "https://api.openai.com/v1",
        history_file: Optional[str] = None,
    ) -> None:
        super().__init__(history_file=history_file)

        if not api_key:
            raise ValueError(
                "api_key 不能为空。请在 config.yaml 填写 api_key，"
                "或设置环境变量 OPENAI_API_KEY。"
            )
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _raise_for_api_error(self, response) -> None:
        if response.status_code == 200:
            return
        try:
            body = response.json()
            err = body.get("error", {})
            code = err.get("code", response.status_code)
            msg = err.get("message", response.text[:200])
        except Exception:
            code = response.status_code
            msg = response.text[:200]
        raise RuntimeError(f"API 错误 [{code}]: {msg}")

    def _stream_chat_impl(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        on_text: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> LLMResponse:
        import requests

        start_time = time.monotonic()

        oai_messages = _to_openai_messages(list(messages))
        if system:
            oai_messages = [{"role": "system", "content": system}] + oai_messages

        payload: dict = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False

        text_buffer = ""
        tool_buffers: dict[str, dict] = {}
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
        }
        stop_reason = "stop"

        try:
            with requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=payload,
                stream=True,
                timeout=180,
            ) as r:
                self._raise_for_api_error(r)

                index_to_id: dict[int, str] = {}

                for raw_line in r.iter_lines():
                    if cancel_event and cancel_event.is_set():
                        break
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if "error" in chunk:
                        err = chunk["error"]
                        raise RuntimeError(
                            f"API 流式错误 [{err.get('code', '?')}]: {err.get('message', '')}"
                        )

                    choices = chunk.get("choices") or []
                    if not choices:
                        u = chunk.get("usage")
                        if u:
                            usage["input_tokens"] = u.get("prompt_tokens", 0)
                            usage["output_tokens"] = u.get("completion_tokens", 0)
                            details = (u.get("completion_tokens_details") or {})
                            usage["reasoning_tokens"] = details.get("reasoning_tokens", 0)
                            prompt_details = (u.get("prompt_tokens_details") or {})
                            usage["cached_tokens"] = prompt_details.get("cached_tokens", 0)
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})
                    text_delta = delta.get("content")
                    if text_delta:
                        text_buffer += text_delta
                        if on_text and not (cancel_event and cancel_event.is_set()):
                            on_text(text_delta)

                    for tc in delta.get("tool_calls") or []:
                        index = tc.get("index", 0)
                        func = tc.get("function") or {}
                        if index not in index_to_id:
                            tool_id = tc.get("id") or f"tool_{index}"
                            index_to_id[index] = tool_id
                            tool_buffers[tool_id] = {
                                "name": func.get("name") or "",
                                "input_json": "",
                            }
                            if on_tool_start:
                                on_tool_start(tool_buffers[tool_id]["name"], tool_id)
                        else:
                            tool_id = index_to_id[index]
                        tool_buffers[tool_id]["input_json"] += func.get("arguments") or ""

                    if choice.get("finish_reason"):
                        stop_reason = choice["finish_reason"]

                    u = chunk.get("usage")
                    if u:
                        usage["input_tokens"] = u.get("prompt_tokens", 0)
                        usage["output_tokens"] = u.get("completion_tokens", 0)
                        details = (u.get("completion_tokens_details") or {})
                        usage["reasoning_tokens"] = details.get("reasoning_tokens", 0)
                        prompt_details = (u.get("prompt_tokens_details") or {})
                        usage["cached_tokens"] = prompt_details.get("cached_tokens", 0)

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"API 请求失败: {e}") from e

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        return LLMResponse(
            text_content=text_buffer,
            tool_calls=_parse_tool_buffers(tool_buffers),
            stop_reason=stop_reason,
            usage=usage,
            elapsed_ms=elapsed_ms,
            cost_usd=0.0,
        )

    def chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ) -> LLMResponse:
        import requests

        start_time = time.monotonic()
        self._record_system_prompt(system)
        self._record_new_messages(messages)

        oai_messages = _to_openai_messages(list(messages))
        if system:
            oai_messages = [{"role": "system", "content": system}] + oai_messages

        payload: dict = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False

        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=payload,
                timeout=180,
            )
            self._raise_for_api_error(r)
            body = r.json()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"API 请求失败: {e}") from e

        choice = body.get("choices", [{}])[0]
        message = choice.get("message", {})
        text_content = message.get("content") or ""
        stop_reason = choice.get("finish_reason", "stop")
        usage_raw = body.get("usage", {})

        output_details = usage_raw.get("completion_tokens_details", {})
        prompt_details = usage_raw.get("prompt_tokens_details", {})

        usage = {
            "input_tokens": usage_raw.get("prompt_tokens", 0),
            "output_tokens": usage_raw.get("completion_tokens", 0),
            "reasoning_tokens": output_details.get("reasoning_tokens", 0),
            "cached_tokens": prompt_details.get("cached_tokens", 0),
        }

        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                name=func.get("name", ""),
                tool_use_id=tc.get("id", ""),
                input=args,
            ))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        response = LLMResponse(
            text_content=text_content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            elapsed_ms=elapsed_ms,
            cost_usd=0.0,
        )
        self._record_response(response)
        return response

    def count_tokens(self, messages: list[dict], system: str = "") -> int:
        total_chars = len(system)
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
        return total_chars // 4 + 10
