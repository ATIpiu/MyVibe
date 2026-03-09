"""工具基类：定义工具接口、返回值格式和注册表。"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """工具执行结果。"""
    content: str
    is_error: bool = False
    metadata: Optional[dict] = None

    # 单次工具结果写入 messages 的最大字符数（约 2000 tokens）
    # 工具结果存入 state.messages 后会随对话历史每次重复发送给 LLM，
    # 过大的结果会在多轮迭代中指数级累积 token，30K→8K 可减少 75% 历史 token。
    MAX_CONTENT_CHARS: int = 8_000

    def to_api_dict(self, tool_use_id: str) -> dict:
        """转为 tool_result 消息格式，超长内容自动截断以控制历史 token 膨胀。"""
        content = self.content or ""
        original_len = len(content)
        if original_len > self.MAX_CONTENT_CHARS:
            content = (
                content[: self.MAX_CONTENT_CHARS]
                + f"\n\n... [内容已截断：原始 {original_len} 字符，"
                f"仅向 LLM 发送前 {self.MAX_CONTENT_CHARS} 字符。"
                f"如需查看后续内容，请使用 offset 参数分页读取]"
            )
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if self.is_error:
            result["is_error"] = True
        return result


class BaseTool(abc.ABC):
    """所有工具的抽象基类。"""

    # 子类必须定义这些类属性
    name: str = ""
    description: str = ""
    input_schema: dict = field(default_factory=dict)

    @abc.abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """执行工具逻辑，子类实现。"""
        ...

    def to_api_dict(self) -> dict:
        """生成 Anthropic API tools 数组中的单项 schema。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """工具注册表：统一管理所有工具类的注册与实例化。"""

    _registry: dict[str, type[BaseTool]] = {}

    @classmethod
    def register(cls, tool_cls: type[BaseTool]) -> type[BaseTool]:
        """装饰器：注册工具类。"""
        cls._registry[tool_cls.name] = tool_cls
        return tool_cls

    @classmethod
    def get(cls, name: str) -> type[BaseTool]:
        """按名称获取工具类。"""
        if name not in cls._registry:
            raise KeyError(f"工具 '{name}' 未注册")
        return cls._registry[name]

    @classmethod
    def all_tools_schema(cls) -> list[dict]:
        """返回所有已注册工具的 API schema 列表。"""
        return [tool_cls().to_api_dict() for tool_cls in cls._registry.values()]

    @classmethod
    def instantiate(cls, name: str, **kwargs) -> BaseTool:
        """实例化指定名称的工具。"""
        tool_cls = cls.get(name)
        return tool_cls(**kwargs)

    @classmethod
    def list_names(cls) -> list[str]:
        """返回所有已注册工具名称列表。"""
        return list(cls._registry.keys())
