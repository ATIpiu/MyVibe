"""上下文管理器：摘要缓存、项目索引、函数搜索。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .file_summary import FileSummary, format_summary, generate_summary, is_stale
from .parsers.language_map import LANGUAGE_MAP
from .parsers.python_parser import FunctionInfo


def _func_to_dict(func: FunctionInfo) -> dict:
    from dataclasses import asdict
    return asdict(func)


def _func_from_dict(d: dict) -> FunctionInfo:
    return FunctionInfo(**d)


class ContextManager:
    """项目级上下文管理器：摘要缓存 + 项目函数索引。"""

    def __init__(
        self,
        project_root: str,
        cache_dir: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        cache_base = Path(cache_dir) if cache_dir else self.project_root / ".agent_cache"
        self.cache_dir = cache_base / "summaries"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 内存索引：file_path -> FileSummary
        self._index: dict[str, FileSummary] = {}

    # ── 文件摘要 ──────────────────────────────────────────────────────────

    def get_file_summary(self, file_path: str) -> Optional[FileSummary]:
        """获取文件摘要，优先从缓存复用。

        流程：内存 → 磁盘缓存 → 重新解析
        """
        resolved = str(Path(file_path).resolve())

        # 内存缓存命中
        if resolved in self._index:
            cached = self._index[resolved]
            if not is_stale(cached, resolved):
                return cached
            # 失效，删除
            del self._index[resolved]

        # 磁盘缓存
        disk_cached = self._load_cache(resolved)
        if disk_cached and not is_stale(disk_cached, resolved):
            self._index[resolved] = disk_cached
            return disk_cached

        # 重新解析
        summary = generate_summary(resolved)
        if summary:
            self._index[resolved] = summary
            self._save_cache(summary)
        return summary

    def get_function_code(
        self,
        file_path: str,
        func_name: str,
        class_name: Optional[str] = None,
    ) -> str:
        """提取指定函数的完整源码。

        Args:
            file_path: 文件路径
            func_name: 函数名
            class_name: 所属类名（方法时提供）

        Returns:
            函数源码字符串，未找到时返回提示信息
        """
        resolved = str(Path(file_path).resolve())
        ext = Path(resolved).suffix.lower()

        if ext == ".py":
            from .parsers.python_parser import extract_function_code
            code = extract_function_code(resolved, func_name, class_name)
        else:
            # 先从摘要中找到行号，再用 generic 提取
            summary = self.get_file_summary(resolved)
            if summary:
                target = next(
                    (f for f in summary.functions if f.name == func_name),
                    None,
                )
                if target:
                    from .parsers.generic_parser import extract_function_code as gec
                    code = gec(resolved, target.start_line, target.end_line)
                else:
                    code = ""
            else:
                code = ""

        if not code:
            return f"未找到函数 '{func_name}'" + (f" (in class {class_name})" if class_name else "")

        rel = Path(resolved).name
        header = f"# {rel}: {func_name} (lines {self._find_start(resolved, func_name)})\n"
        return header + code

    def _find_start(self, file_path: str, func_name: str) -> str:
        """从索引中查找函数起始行信息。"""
        summary = self._index.get(file_path)
        if summary:
            for f in summary.functions:
                if f.name == func_name:
                    return f"{f.start_line}-{f.end_line}"
        return "?"

    # ── 项目索引 ──────────────────────────────────────────────────────────

    def index_project(self, glob_pattern: str = "**/*.py") -> dict[str, FileSummary]:
        """扫描项目所有源码文件，批量生成摘要索引。

        Args:
            glob_pattern: 文件匹配模式，默认扫描所有 .py 文件

        Returns:
            file_path -> FileSummary 映射
        """
        # 支持多个扩展名
        if glob_pattern == "**/*":
            patterns = [f"**/*{ext}" for ext in LANGUAGE_MAP.keys()]
        else:
            patterns = [glob_pattern]

        for pattern in patterns:
            for file_path in self.project_root.glob(pattern):
                # 跳过隐藏目录和缓存
                skip_dirs = {".git", ".agent_cache", "__pycache__", "node_modules", ".venv", "venv"}
                if any(part in skip_dirs for part in file_path.parts):
                    continue
                if file_path.is_file():
                    self.get_file_summary(str(file_path))

        return dict(self._index)

    def search_functions(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[tuple[str, FunctionInfo]]:
        """在索引中按名称/描述模糊搜索函数。

        Args:
            query: 搜索关键词（支持函数名和描述文字）
            top_k: 最多返回结果数

        Returns:
            [(file_path, FunctionInfo), ...] 列表
        """
        query_lower = query.lower()
        results: list[tuple[int, str, FunctionInfo]] = []

        for file_path, summary in self._index.items():
            for func in summary.functions:
                score = 0
                name_lower = func.name.lower()
                desc_lower = func.description.lower()

                # 精确名称匹配得分最高
                if query_lower == name_lower:
                    score = 100
                elif query_lower in name_lower:
                    score = 60
                elif name_lower in query_lower:
                    score = 40

                # 描述匹配
                if query_lower in desc_lower:
                    score += 30

                # 关键词分词匹配
                keywords = re.split(r"[\s_\-/\\]+", query_lower)
                for kw in keywords:
                    if kw and kw in name_lower:
                        score += 10
                    if kw and kw in desc_lower:
                        score += 5

                if score > 0:
                    results.append((score, file_path, func))

        results.sort(key=lambda x: -x[0])
        return [(fp, func) for _, fp, func in results[:top_k]]

    def invalidate(self, file_path: str) -> None:
        """使指定文件的缓存失效（edit_file 后调用）。"""
        resolved = str(Path(file_path).resolve())
        self._index.pop(resolved, None)
        # 删除磁盘缓存
        cache_key = resolved.replace("/", "__").replace("\\", "__").replace(":", "")
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            cache_file.unlink(missing_ok=True)

    # ── 缓存序列化 ────────────────────────────────────────────────────────

    def _cache_path(self, file_path: str) -> Path:
        safe_name = file_path.replace("/", "__").replace("\\", "__").replace(":", "")
        return self.cache_dir / f"{safe_name}.json"

    def _save_cache(self, summary: FileSummary) -> None:
        """将摘要序列化写入磁盘缓存。"""
        cache_file = self._cache_path(summary.file_path)
        data = {
            "file_path": summary.file_path,
            "file_hash": summary.file_hash,
            "language": summary.language,
            "line_count": summary.line_count,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "functions": [_func_to_dict(f) for f in summary.functions],
        }
        try:
            cache_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_cache(self, file_path: str) -> Optional[FileSummary]:
        """从磁盘加载缓存的摘要。"""
        cache_file = self._cache_path(file_path)
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            functions = [_func_from_dict(f) for f in data.get("functions", [])]
            return FileSummary(
                file_path=data["file_path"],
                language=data.get("language", "generic"),
                functions=functions,
                line_count=data.get("line_count", 0),
                file_hash=data["file_hash"],
            )
        except Exception:
            return None
