"""记忆系统数据模型（层次化结构：模块 → 函数）。

设计原则：以节省 token 为第一要务。
  - FunctionData：qualname（作为 key） + 一句话描述
  - ModuleData：一句话描述 + 函数字典
  - 调用关系存 call_graph.json，不冗余进函数条目
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionData:
    """函数级记忆条目。key = qualname（含类名，如 MyClass.method）。"""

    purpose: str       # 一句话用途（docstring 第一行）

    def to_dict(self) -> dict:
        return {"purpose": self.purpose}

    @classmethod
    def from_dict(cls, d: dict) -> "FunctionData":
        return cls(purpose=d.get("purpose", ""))


@dataclass
class ModuleData:
    """模块级记忆条目，包含该模块所有函数。"""

    purpose: str       # 一句话描述（模块 docstring 第一行）
    functions: dict[str, FunctionData] = field(default_factory=dict)  # qualname -> FunctionData

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "functions": {q: f.to_dict() for q, f in self.functions.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModuleData":
        funcs = {
            q: FunctionData.from_dict(v)
            for q, v in d.get("functions", {}).items()
        }
        return cls(
            purpose=d.get("purpose", ""),
            functions=funcs,
        )


@dataclass
class CallEdge:
    """调用关系图的一条边。格式：module_path:qualname"""

    caller: str   # 调用方 key（module:qualname）
    callee: str   # 被调用方 key

    def to_dict(self) -> dict:
        return {"caller": self.caller, "callee": self.callee}

    @classmethod
    def from_dict(cls, d: dict) -> "CallEdge":
        return cls(caller=d.get("caller", ""), callee=d.get("callee", ""))
