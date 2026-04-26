"""代码索引管理器：CRUD + 自动同步 + 调用图维护。

存储格式：memory_tree.json（嵌套路径树，节省 token）。
存储路径：{project_root}/.vibecoding/memory/
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .ast_analyzer import AstAnalyzer
from .models import FunctionData, ModuleData
from .tree_storage import TreeStorage

_SYNCABLE_EXTENSIONS = {".py"}

_instances: dict[str, "IndexManager"] = {}
_instances_lock = threading.Lock()


def get_index_manager(project_root: str) -> "IndexManager":
    """获取或创建指定项目根目录的 IndexManager 单例。"""
    with _instances_lock:
        if project_root not in _instances:
            _instances[project_root] = IndexManager(project_root)
        return _instances[project_root]


class IndexManager:
    """代码索引管理器：协调 TreeStorage + AstAnalyzer，对外暴露查询/更新接口。"""

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root)
        self._index_dir = self.project_root / ".vibecoding" / "memory"
        self._storage = TreeStorage(self._index_dir)
        self._analyzer = AstAnalyzer()

    # ──────────────────────────────── 读取接口 ────────────────────────────────

    def read_all(self) -> dict[str, ModuleData]:
        return self._storage.get_all()

    def read_module(self, module_path: str) -> Optional[ModuleData]:
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

    def find_qualname_candidates(
        self, module_path: str, partial: str, limit: int = 8
    ) -> list[str]:
        """模糊查找：返回模块内所有以 ``partial`` 结尾或包含它的 qualname。

        用法：模型给出 function_key 不精确（例如丢了类前缀）时，由
        ``read_index(scope='function')`` 调用此方法生成候选提示。
        """
        module = self._storage.get_module(module_path)
        if module is None or not partial:
            return []
        suffix_hits, contains_hits = [], []
        for q in module.functions:
            if q == partial or q.endswith("." + partial):
                suffix_hits.append(q)
            elif partial in q:
                contains_hits.append(q)
        return (suffix_hits + contains_hits)[:limit]

    def get_callers(self, function_key: str) -> list[str]:
        return self._storage.get_callers(function_key)

    def get_callees(self, function_key: str) -> list[str]:
        return self._storage.get_callees(function_key)

    def search(
        self,
        query: str = "",
        top_k: int = 20,
    ) -> list[tuple[str, str, FunctionData]]:
        return self._storage.search(query=query, top_k=top_k)

    def render_overview(self) -> str:
        return self._storage.render_overview_text()

    def get_function_ranges(self, module_path: str) -> dict[str, tuple[int, int]]:
        file_path = self.project_root / module_path
        if not file_path.exists():
            return {}
        return self._analyzer.get_function_ranges(file_path)

    def render_tree(self) -> str:
        return self._storage.render_tree_text()

    # ──────────────────────────────── 同步接口 ────────────────────────────────

    def sync(self, file_path: str | Path | None = None) -> dict:
        """同步索引。file_path=None 时全项目扫描，否则只同步单文件。"""
        if file_path is None:
            return self._sync_project()
        return self._sync_file(Path(file_path))

    def _sync_file(self, path: Path) -> dict:
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
        self._storage.set_edges_for_module(rel_str, calls_map)

        return {
            "files_processed": 1,
            "module": rel_str,
            "functions_count": len(module_data.functions),
        }

    def _sync_project(self, glob_pattern: str = "**/*.py") -> dict:
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
        try:
            rel_str = str(path.relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return {"files_processed": 0}
        self._storage.delete_module(rel_str)
        return {"files_processed": 0, "module_deleted": rel_str}


def _should_skip(path: Path) -> bool:
    skip_parts = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".vibecoding", "dist", "build",
        ".agent_cache", ".agent_sessions", "logs",
    }
    return any(part in skip_parts for part in path.parts)
