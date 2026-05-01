"""OpenAI 兼容接口客户端实现。

适用于所有兼容 OpenAI Chat Completions API 的服务（OpenAI、智谱、DeepSeek 等）。
依赖 base_client.LLMClient，支持流式 + 非流式调用。

重试策略：
  - 最多重试 6 次，指数退避 1/2/4/8/16/32 秒（累计 ~63 秒）
  - 可重试：ChunkedEncodingError / ConnectionError / Timeout / 其他非 API 错误
  - 不重试：RuntimeError（由 _raise_for_api_error 抛出的 4xx/5xx API 错误）
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable, Optional

from .base_client import LLMClient, LLMResponse, ToolCall


_MAX_RETRIES = 6
_RETRY_DELAYS = [1, 2, 4, 8, 16, 32]  # 指数退避秒数，累计约 63s，覆盖后端短暂重启


# 兜底：vLLM 在 Qwen3/DeepSeek-R1 等思考模型上如果未启用 --reasoning-parser，
# 会把整块 <tool_call> XML 当 reasoning 吞掉，tool_calls 字段返回空。
# 此函数从文本里反向提取 Hermes/Qwen3 XML 格式的 <tool_call> 调用。
_TOOL_CALL_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FUNCTION_NAME = re.compile(r"<function=([^>]+)>")
_PARAMETER = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.DOTALL)


def _parse_hermes_tool_calls(text: str) -> list[ToolCall]:
    """从 Hermes/Qwen3 XML 格式文本里解析出 tool_calls。无匹配返回空列表。"""
    results: list[ToolCall] = []
    if not text or "<tool_call>" not in text:
        return results
    for idx, block in enumerate(_TOOL_CALL_BLOCK.findall(text)):
        name_m = _FUNCTION_NAME.search(block)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        params: dict = {}
        for key, value in _PARAMETER.findall(block):
            params[key.strip()] = value.strip()
        results.append(ToolCall(
            name=name, tool_use_id=f"fallback_{idx}", input=params,
        ))
    return results


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试。RuntimeError 是 API 层错误（4xx/5xx），不重试。"""
    if isinstance(exc, RuntimeError):
        return False
    try:
        import requests.exceptions
        return isinstance(exc, (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ReadTimeout,
        ))
    except ImportError:
        return True  # requests 未安装时兜底重试


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
        extra_body: Optional[dict] = None,
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
        # 附加请求体字段（OpenAI 官方忽略未知字段；vLLM/Qwen 等部署用这个传
        # chat_template_kwargs 等自定义参数）
        self.extra_body = extra_body or {}

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]:
        """检测 base_url 后端是否可达（GET /models）。返回 (ok, detail)。"""
        import requests
        try:
            r = requests.get(
                f"{self.base_url}/models",
                headers=self._build_headers(),
                timeout=timeout,
            )
            if r.status_code < 500:
                return True, f"HTTP {r.status_code}"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

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
        system_str = self._system_to_str(system)
        if system_str:
            oai_messages = [{"role": "system", "content": system_str}] + oai_messages

        payload: dict = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False
        # merge extra_body（不覆盖已设字段）
        for k, v in self.extra_body.items():
            payload.setdefault(k, v)

        last_exc: Exception = RuntimeError("未知错误")

        for attempt in range(_MAX_RETRIES + 1):
            # 每次重试前重置所有缓冲区
            text_buffer = ""
            reasoning_buffer = ""  # 累积 reasoning/reasoning_content，用于兜底解析
            tool_buffers: dict[str, dict] = {}
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
            }
            stop_reason = "stop"

            # 重试时不回调 on_text，避免终端输出重复内容
            _on_text = on_text if attempt == 0 else None
            _on_tool_start = on_tool_start if attempt == 0 else None

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
                            if _on_text and not (cancel_event and cancel_event.is_set()):
                                _on_text(text_delta)

                        # 累积 reasoning 字段（Qwen3/DeepSeek-R1 等思考模型 vLLM 返回），
                        # 若结尾 content+tool_calls 双空则从中解析 <tool_call> 兜底
                        r_delta = delta.get("reasoning") or delta.get("reasoning_content")
                        if r_delta:
                            reasoning_buffer += r_delta

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
                                if _on_tool_start:
                                    _on_tool_start(tool_buffers[tool_id]["name"], tool_id)
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

                # 成功，跳出重试循环
                break

            except RuntimeError:
                raise  # API 层错误（4xx/5xx），直接抛出不重试
            except Exception as e:
                last_exc = e
                if not _is_retryable(e):
                    raise RuntimeError(f"API 请求失败: {e}") from e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(
                        f"\n[重试 {attempt + 1}/{_MAX_RETRIES}] 网络中断（{type(e).__name__}），"
                        f"{delay}s 后重试...",
                        flush=True,
                    )
                    time.sleep(delay)
                    # 重试时补回 on_text 输出（从头重新流式打印）
                    _on_text = on_text
                    _on_tool_start = on_tool_start
                else:
                    raise RuntimeError(f"API 请求失败（已重试 {_MAX_RETRIES} 次）: {last_exc}") from last_exc

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        final_tool_calls = _parse_tool_buffers(tool_buffers)

        # 兜底：content/tool_calls 均空但 reasoning 含 <tool_call> XML 时从中提取
        # （vLLM 未开 --reasoning-parser 导致 Qwen3 tool_call 被扔进 reasoning 的场景）
        if not text_buffer and not final_tool_calls and reasoning_buffer:
            recovered = _parse_hermes_tool_calls(reasoning_buffer)
            if recovered:
                print(
                    f"\n[openai_client] 从 reasoning 兜底解析出 {len(recovered)} 个 tool_call",
                    flush=True,
                )
                final_tool_calls = recovered
                stop_reason = "tool_calls"

        return LLMResponse(
            text_content=text_buffer,
            tool_calls=final_tool_calls,
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
        system_str = self._system_to_str(system)
        if system_str:
            oai_messages = [{"role": "system", "content": system_str}] + oai_messages

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
        for k, v in self.extra_body.items():
            payload.setdefault(k, v)

        last_exc: Exception = RuntimeError("未知错误")
        body: dict = {}

        for attempt in range(_MAX_RETRIES + 1):
            try:
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._build_headers(),
                    json=payload,
                    timeout=180,
                )
                self._raise_for_api_error(r)
                body = r.json()
                break  # 成功
            except RuntimeError:
                raise
            except Exception as e:
                last_exc = e
                if not _is_retryable(e):
                    raise RuntimeError(f"API 请求失败: {e}") from e
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    print(
                        f"\n[重试 {attempt + 1}/{_MAX_RETRIES}] 网络中断（{type(e).__name__}），"
                        f"{delay}s 后重试...",
                        flush=True,
                    )
                    time.sleep(delay)
                else:
                    raise RuntimeError(f"API 请求失败（已重试 {_MAX_RETRIES} 次）: {last_exc}") from last_exc

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

        # 兜底：同流式路径——content/tool_calls 空但 reasoning 含 <tool_call> 则解析
        if not text_content and not tool_calls:
            reasoning_text = message.get("reasoning") or message.get("reasoning_content") or ""
            if reasoning_text:
                recovered = _parse_hermes_tool_calls(reasoning_text)
                if recovered:
                    print(
                        f"[openai_client] 从 reasoning 兜底解析出 {len(recovered)} 个 tool_call",
                        flush=True,
                    )
                    tool_calls = recovered
                    stop_reason = "tool_calls"

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

    def count_tokens(self, messages: list[dict], system="") -> int:
        total_chars = len(self._system_to_str(system))
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", "") or block.get("content", ""))
        return total_chars // 4 + 10
