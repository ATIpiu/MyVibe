"""智谱 AI (GLM) 客户端实现。

依赖 base_client.LLMClient，兼容智谱 OpenAI SSE 协议。
包含：
  - ZhipuLLMClient：流式 + 非流式调用实现
  - _to_openai_messages / _to_openai_tools / _parse_tool_buffers：格式转换工具
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional, Tuple

from .base_client import LLMClient, LLMResponse, ToolCall


# ── 格式转换工具函数 ───────────────────────────────────────────────────────
# (保持不变)
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


# ── 智谱 GLM 定价逻辑更新 ────────────────────────────────────────────────────────
# 价格单位：元 / 1M tokens
# 结构：{
#     "model_name": {
#         input_tier: { output_tier: (input_price, output_price) }
#     }
# }
# input_tier: 0 代表 [0, 32k), 1 代表 [32k+)
# output_tier: 0 代表 [0, 0.2k), 1 代表 [0.2k+)

_ZHIPU_PRICING_TIERED = {
    "glm-5": {
        0: {0: (4, 18), 1: (4, 18)}, # 输出无论长短，价格一致
        1: {0: (6, 22), 1: (6, 22)}
    },
    "glm-5-code": {
        0: {0: (6, 28), 1: (6, 28)},
        1: {0: (8, 32), 1: (8, 32)}
    },
    "glm-4.7": {
        # 输入 < 32k
        0: {
            0: (2, 8),   # 输出 < 0.2k
            1: (2, 14)   # 输出 >= 0.2k
        },
        # 输入 >= 32k (表格显示 32-200k 范围，暂按此处理)
        1: {
            0: (4, 16),
            1: (4, 16)
        }
    },
    "glm-4.5-air": {
        0: {
            0: (0.8, 2),
            1: (0.8, 6)  # 输出 >= 0.2k 价格不同
        },
        1: { # 输入 32-128k
            0: (1.2, 8),
            1: (1.2, 8)
        }
    },
    "glm-4.7-flashx": {
        # 200K 上下文，表格未明确区分输入阶梯，统一价
        0: {0: (0.5, 3), 1: (0.5, 3)},
        1: {0: (0.5, 3), 1: (0.5, 3)}
    },
    "glm-4.7-flash": {
        0: {0: (0.0, 0.0), 1: (0.0, 0.0)}, # 免费
        1: {0: (0.0, 0.0), 1: (0.0, 0.0)}
    },
    "default": { # 默认回退价格
        0: {0: (1.0, 4.0), 1: (1.0, 4.0)},
        1: {0: (1.0, 4.0), 1: (1.0, 4.0)}
    }
}

class ZhipuLLMClient(LLMClient):
    """智谱 AI (GLM) 客户端，兼容 OpenAI SSE 协议。"""

    def __init__(
        self,
        model: str = "glm-4.7",
        api_key: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 1.0,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        enable_thinking: bool = True,
        history_file: Optional[str] = None,
    ) -> None:
        super().__init__(history_file=history_file)

        if not api_key:
            raise ValueError(
                "api_key 不能为空。请在 config.yaml 填写 api_key，"
                "或设置环境变量 ZHIPU_API_KEY。"
            )
        self.model = model.lower() # 统一转小写匹配 key
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.enable_thinking = enable_thinking

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
        raise RuntimeError(f"智谱 API 错误 [{code}]: {msg}")

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
        if self.enable_thinking:
            payload["thinking"] = {"type": "enabled"}
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"

        text_buffer = ""
        tool_buffers: dict[str, dict] = {}
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0
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

                for raw_line in r.iter_lines():
                    if cancel_event and cancel_event.is_set():
                        break
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if "error" in chunk:
                        err = chunk["error"]
                        raise RuntimeError(
                            f"智谱 API 流式错误 [{err.get('code', '?')}]: {err.get('message', '')}"
                        )

                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    text_delta = delta.get("content")
                    if text_delta:
                        text_buffer += text_delta
                        if on_text:
                            on_text(text_delta)

                    for tc in delta.get("tool_calls", []):
                        tool_id = tc.get("id") or f"tool_{tc.get('index', 0)}"
                        func = tc.get("function", {})
                        if tool_id not in tool_buffers:
                            tool_buffers[tool_id] = {"name": func.get("name", ""), "input_json": ""}
                            if on_tool_start:
                                on_tool_start(tool_buffers[tool_id]["name"], tool_id)
                        tool_buffers[tool_id]["input_json"] += func.get("arguments", "")

                    if choice.get("finish_reason"):
                        stop_reason = choice["finish_reason"]

                    if "usage" in chunk:
                        u = chunk["usage"]
                        usage["input_tokens"] = u.get("prompt_tokens", 0)
                        usage["output_tokens"] = u.get("completion_tokens", 0)
                        details = u.get("completion_tokens_details", {})
                        usage["reasoning_tokens"] = details.get("reasoning_tokens", 0)
                        prompt_details = u.get("prompt_tokens_details", {})
                        usage["cached_tokens"] = prompt_details.get("cached_tokens", 0)

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"智谱 API 请求失败: {e}") from e

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        return LLMResponse(
            text_content=text_buffer,
            tool_calls=_parse_tool_buffers(tool_buffers),
            stop_reason=stop_reason,
            usage=usage,
            elapsed_ms=elapsed_ms,
            cost_usd=self.calc_cost(
                usage["input_tokens"],
                usage["output_tokens"],
                reasoning_tokens=usage["reasoning_tokens"]
            ),
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
        if self.enable_thinking:
            payload["thinking"] = {"type": "enabled"}
        if tools:
            payload["tools"] = _to_openai_tools(tools)
            payload["tool_choice"] = "auto"

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
            raise RuntimeError(f"智谱 API 请求失败: {e}") from e

        choice = body.get("choices", [{}])[0]
        message = choice.get("message", {})
        text_content = message.get("content") or ""
        stop_reason = choice.get("finish_reason", "stop")
        usage_raw = body.get("usage", {})

        output_details = usage_raw.get("completion_tokens_details", {})
        prompt_details = usage_raw.get("prompt_tokens_details", {})

        reasoning_tokens = output_details.get("reasoning_tokens", 0)
        cached_tokens = prompt_details.get("cached_tokens", 0)

        usage = {
            "input_tokens": usage_raw.get("prompt_tokens", 0),
            "output_tokens": usage_raw.get("completion_tokens", 0),
            "reasoning_tokens": reasoning_tokens,
            "cached_tokens": cached_tokens,
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
            cost_usd=self.calc_cost(
                usage["input_tokens"],
                usage["output_tokens"],
                reasoning_tokens=usage["reasoning_tokens"]
            ),
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

    def calc_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0
    ) -> float:
        """
        根据智谱最新价格表计算成本。
        支持 GLM-5, GLM-4.7, GLM-4.5-Air 等模型的阶梯定价。
        """
        # 1. 获取模型价格表
        pricing_model = _ZHIPU_PRICING_TIERED.get(self.model, _ZHIPU_PRICING_TIERED["default"])

        # 2. 确定输入阶梯
        # 表格中 [0, 32) 单位为 K，即 [0, 32000)
        if input_tokens < 32000:
            input_tier = 0
        else:
            input_tier = 1

        # 3. 确定输出阶梯
        # 表格中 [0, 0.2) 单位为 K，即 [0, 200)
        # reasoning_tokens 计入总输出，决定是否触发高价档位
        total_output = output_tokens + reasoning_tokens

        if total_output < 200:
            output_tier = 0
        else:
            output_tier = 1

        # 4. 获取单价 (元 / 1M tokens)
        input_price, output_price = pricing_model.get(input_tier, pricing_model[0])[output_tier]

        # 5. 计算最终费用
        # 注意：价格表是元/百万tokens，所以除以 1,000,000
        cost = (input_tokens * input_price + total_output * output_price) / 1_000_000

        return cost