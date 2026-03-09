"""记忆存储层：读写 .vibecoding/memory/ 目录下的 JSON 文件。

JSON 格式：紧凑（无缩进/空格），以节省 token。
层次结构：memory.json = {module_path: ModuleData}（一级=模块，二级=函数）
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .models import CallEdge, FunctionData, ModuleData


class MemoryStorage:
    """管理 .vibecoding/memory/ 目录下的 JSON 索引文件。

    文件布局：
        .vibecoding/memory/
            memory.json        层次化记忆（模块 → 函数）
            call_graph.json    函数调用关系图
    """

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._memory_path = self._dir / "memory.json"
        self._call_graph_path = self._dir / "call_graph.json"

        self._memory: dict[str, ModuleData] = {}
        self._call_edges: list[CallEdge] = []

        self._load_all()

    # ── 加载 ────────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        self._memory = self._load_memory()
        self._call_edges = self._load_call_graph()

    def _load_memory(self) -> dict[str, ModuleData]:
        raw = self._read_json(self._memory_path, {})
        return {k: ModuleData.from_dict(v) for k, v in raw.items()}

    def _load_call_graph(self) -> list[CallEdge]:
        raw = self._read_json(self._call_graph_path, {"edges": []})
        return [CallEdge.from_dict(e) for e in raw.get("edges", [])]

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8")) or default
        except (json.JSONDecodeError, OSError):
            return default

    # ── 保存（紧凑 JSON，无缩进） ───────────────────────────────────────────

    def _save_memory(self) -> None:
        self._write_json(self._memory_path, {k: v.to_dict() for k, v in self._memory.items()})

    def _save_call_graph(self) -> None:
        self._write_json(self._call_graph_path, {"edges": [e.to_dict() for e in self._call_edges]})

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ── 模块 CRUD ───────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, ModuleData]:
        """返回全部记忆（只读副本）。"""
        return dict(self._memory)

    def get_module(self, module_path: str) -> Optional[ModuleData]:
        return self._memory.get(module_path)

    def get_function(self, module_path: str, qualname: str) -> Optional[FunctionData]:
        module = self._memory.get(module_path)
        if module is None:
            return None
        return module.functions.get(qualname)

    def upsert_module(self, module_path: str, module: ModuleData) -> None:
        """写入或更新整个模块（含其函数）。线程安全。"""
        with self._lock:
            self._memory[module_path] = module
            self._save_memory()

    def delete_module(self, module_path: str) -> None:
        """删除模块及其所有函数的记忆，并级联删除调用图边。"""
        with self._lock:
            if module_path not in self._memory:
                return
            # 找出该模块的所有 function key（module:qualname）
            module_keys = {
                f"{module_path}:{q}"
                for q in self._memory[module_path].functions
            }
            del self._memory[module_path]
            # 级联清除调用图边
            self._call_edges = [
                e for e in self._call_edges
                if e.caller not in module_keys and e.callee not in module_keys
            ]
            self._save_memory()
            self._save_call_graph()

    # ── 调用图 ──────────────────────────────────────────────────────────────

    def all_edges(self) -> list[CallEdge]:
        return list(self._call_edges)

    def set_edges_for_module(self, module_path: str, calls_map: dict[str, list[str]]) -> None:
        """替换某个模块所有函数的出向边。calls_map = {qualname: [callee_key, ...]}"""
        with self._lock:
            module_prefix = f"{module_path}:"
            # 删除该模块所有出向边
            self._call_edges = [e for e in self._call_edges if not e.caller.startswith(module_prefix)]
            # 写入新边
            for qualname, callees in calls_map.items():
                caller_key = f"{module_path}:{qualname}"
                for callee in set(callees):
                    self._call_edges.append(CallEdge(caller=caller_key, callee=callee))
            self._save_call_graph()

    def get_callers(self, function_key: str) -> list[str]:
        """返回调用 function_key 的所有 key。"""
        return [e.caller for e in self._call_edges if e.callee == function_key]

    def get_callees(self, function_key: str) -> list[str]:
        """返回 function_key 调用的所有 key。"""
        return [e.callee for e in self._call_edges if e.caller == function_key]

    # ── 搜索 ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        top_k: int = 20,
    ) -> list[tuple[str, str, FunctionData]]:
        """按关键词搜索函数，返回 [(module_path, qualname, FunctionData), ...]。"""
        query_lower = query.lower()
        results: list[tuple[str, str, FunctionData]] = []
        for module_path, module in self._memory.items():
            for qualname, func in module.functions.items():
                if query_lower:
                    text = f"{qualname} {func.purpose} {module_path}".lower()
                    if query_lower not in text:
                        continue
                results.append((module_path, qualname, func))
        return results[:top_k]

    def reload(self) -> None:
        with self._lock:
            self._load_all()

