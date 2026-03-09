"""核心记忆管理器：CRUD + 自动同步 + 调用图维护。"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .ast_analyzer import AstAnalyzer
from .models import FunctionData, ModuleData
from .storage import MemoryStorage

# 支持自动同步的文件扩展名
_SYNCABLE_EXTENSIONS = {".py"}

# 全局单例（按 project_root 键控）
_instances: dict[str, "MemoryManager"] = {}
_instances_lock = threading.Lock()


def get_memory_manager(project_root: str) -> "MemoryManager":
    """获取或创建指定项目根目录的 MemoryManager 单例。"""
    with _instances_lock:
        if project_root not in _instances:
            _instances[project_root] = MemoryManager(project_root)
        return _instances[project_root]


class MemoryManager:
    """记忆管理器：协调 Storage + AstAnalyzer，对外暴露查询/更新接口。

    存储路径：{project_root}/.vibecoding/memory/
    JSON 结构：memory.json = {module_path: ModuleData}
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)
        self._memory_dir = self.project_root / ".vibecoding" / "memory"
        self._storage = MemoryStorage(self._memory_dir)
        self._analyzer = AstAnalyzer()

    # ──────────────────────────────── 读取接口 ────────────────────────────────

    def read_all(self) -> dict[str, ModuleData]:
        """返回全部记忆（模块→函数层次结构）。"""
        return self._storage.get_all()

    def read_module(self, module_path: str) -> Optional[ModuleData]:
        """返回指定模块的记忆（含其所有函数）。"""
        return self._storage.get_module(module_path)

    def read_function_source(self, function_key: str) -> Optional[str]:
        """获取函数完整源码。function_key 格式：module_path:qualname"""
        parts = function_key.split(":", 1)
        if len(parts) != 2:
            return None
        module_path, qualname = parts
        file_path = self.project_root / module_path
        if not file_path.exists():
            return None
        return self._analyzer.get_function_source(file_path, qualname)

    def get_callers(self, function_key: str) -> list[str]:
        """返回所有调用该函数的 key 列表。"""
        return self._storage.get_callers(function_key)

    def get_callees(self, function_key: str) -> list[str]:
        """返回该函数调用的所有 key 列表。"""
        return self._storage.get_callees(function_key)

    def search(
        self,
        query: str = "",
        top_k: int = 20,
    ) -> list[tuple[str, str, FunctionData]]:
        """按关键词搜索函数，返回 [(module_path, qualname, FunctionData), ...]。"""
        return self._storage.search(query=query, top_k=top_k)

    # ──────────────────────────────── 同步接口 ────────────────────────────────

    def sync(self, file_path: str | Path | None = None) -> dict:
        """同步记忆。file_path=None 时全项目扫描，否则只同步单文件。"""
        if file_path is None:
            return self._sync_project()
        return self._sync_file(Path(file_path))

    def _sync_file(self, path: Path) -> dict:
        """解析单个文件并同步记忆。"""
        if path.suffix not in _SYNCABLE_EXTENSIONS:
            return {"files_processed": 0, "skipped": "unsupported extension"}
        if not path.exists():
            return self._remove_module(path)

        try:
            rel_path = path.relative_to(self.project_root)
        except ValueError:
            rel_path = path
        rel_str = str(rel_path).replace("\\", "/")

        module_data, calls_map = self._analyzer.analyze_file(path, self.project_root)
        self._storage.upsert_module(rel_str, module_data)
        # calls_map key 是 qualname，需要转为完整 key（module:qualname）
        full_calls_map = {qualname: callees for qualname, callees in calls_map.items()}
        self._storage.set_edges_for_module(rel_str, full_calls_map)

        return {
            "files_processed": 1,
            "module": rel_str,
            "functions_count": len(module_data.functions),
        }

    def _sync_project(self, glob_pattern: str = "**/*.py") -> dict:
        """扫描整个项目，批量同步所有匹配文件。"""
        files_processed = 0
        total_functions = 0

        for file_path in self.project_root.glob(glob_pattern):
            if _should_skip(file_path):
                continue
            result = self._sync_file(file_path)
            if result.get("files_processed", 0):
                files_processed += 1
                total_functions += result.get("functions_count", 0)

        return {
            "files_processed": files_processed,
            "total_functions": total_functions,
            "total_modules": len(self._storage.get_all()),
        }

    # ──────────────────────────────── 内部工具 ────────────────────────────────

    def _remove_module(self, path: Path) -> dict:
        """文件被删除时，清理相关记忆。"""
        try:
            rel_str = str(path.relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return {"files_processed": 0}

        self._storage.delete_module(rel_str)
        return {"files_processed": 0, "module_deleted": rel_str}


def _should_skip(path: Path) -> bool:
    """跳过不需要分析的路径。"""
    skip_parts = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".vibecoding", "dist", "build",
        ".agent_cache", ".agent_sessions", "logs",
    }
    return any(part in skip_parts for part in path.parts)
