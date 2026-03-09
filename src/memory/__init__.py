"""VibeCoding 代码记忆管理系统。

两层架构：
- 索引层：memory.json（模块→函数层次结构）+ call_graph.json（调用关系）
- 详情层：实际源码文件（按需读取）
"""
from .memory_manager import MemoryManager

__all__ = ["MemoryManager"]
