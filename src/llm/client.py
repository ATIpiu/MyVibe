"""LLM 客户端工厂 + 统一导入入口。

其他模块只需从此文件导入，无需感知具体实现文件：
    from src.llm.client import LLMClient, LLMResponse, create_client_from_config

扩展新模型后端：
    1. 在 src/llm/ 下创建新文件，实现 LLMClient 子类
    2. 在此文件的 _PROVIDER_MAP 中注册
    3. config.yaml 设置 provider: <name> 即可，调用方无需修改
"""
from __future__ import annotations

import os

# ── 公共数据类（re-export） ────────────────────────────────────────────────
from .base_client import (
    LLMClient,
    LLMResponse,
    StreamEvent,
    ToolCall,
    HistoryEntry,
)

# ── 具体实现（re-export） ─────────────────────────────────────────────────
from .openai_client import OpenAIClient

__all__ = [
    "LLMClient",
    "LLMResponse",
    "StreamEvent",
    "ToolCall",
    "HistoryEntry",
    "OpenAIClient",
    "create_client_from_config",
    "register_provider",
]

# ── 提供商注册表 ───────────────────────────────────────────────────────────

_PROVIDER_MAP: dict[str, type[LLMClient]] = {
    "openai": OpenAIClient,
}


def create_client_from_config(config: dict) -> LLMClient:
    """根据配置字典创建对应的 LLMClient 实现。

    必填字段：
        api_key   - API 密钥（也可通过环境变量 OPENAI_API_KEY 设置）

    可选字段：
        provider, model, max_tokens, temperature, base_url, history_file, extra_body
    """
    provider = config.get("provider", "openai").lower()
    api_key = (
        config.get("api_key")
        or os.environ.get("OPENAI_API_KEY")
    )

    cls = _PROVIDER_MAP.get(provider, OpenAIClient)
    return cls(
        model=config.get("model", "gpt-4o"),
        api_key=api_key,
        max_tokens=config.get("max_tokens", 8192),
        temperature=config.get("temperature", 1.0),
        base_url=config.get("base_url", "https://api.openai.com/v1"),
        history_file=config.get("history_file", "logs/conversation_history.jsonl"),
        extra_body=config.get("extra_body") or {},
    )


def register_provider(name: str, cls: type[LLMClient]) -> None:
    """注册自定义 LLM 后端。

    用法：
        class MyClient(LLMClient): ...
        register_provider("my_model", MyClient)
    然后在 config.yaml 设置 provider: my_model 即可。
    """
    _PROVIDER_MAP[name.lower()] = cls
