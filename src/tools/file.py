"""文件操作工具：读取、写入、编辑、搜索。"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from .base_tool import BaseTool, ToolRegistry, ToolResult
from ..utils.path import safe_resolve


def _auto_sync_memory(file_path: str) -> None:
    """写入/编辑成功后同步记忆索引（仅对 .py 文件）。"""
    if not file_path.endswith(".py"):
        return
    try:
        from .memory_tools import _manager
        if _manager is None:
            return
        result = _manager.sync(file_path)
        count = result.get("functions_count", 0)
        if count is not None:
            print(f"  [memory] 同步 {file_path}: {count} 个函数", flush=True)
    except Exception as e:
        print(f"  [memory] 同步失败 {file_path}: {e}", flush=True)

def _get_edit_context(file_path: Path, new_string: str, context_lines: int = 3) -> str:
    """返回 new_string 在文件中的所在位置及前后上下文行。

    格式：行号→内容，若 new_string 超过 20 行只显示前 5 行。
    """
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        new_lines = new_string.splitlines()
        if not new_lines:
            return ""

        # 在文件行列表中搜索 new_string 的起始行
        first_line = new_lines[0]
        match_lineno = -1
        for i, line in enumerate(lines):
            if first_line in line:
                # 验证后续行也匹配（多行情形）
                if all(
                    i + j < len(lines) and nl in lines[i + j]
                    for j, nl in enumerate(new_lines[: len(new_lines)])
                ):
                    match_lineno = i
                    break

        if match_lineno == -1:
            return ""

        new_line_count = len(new_lines)
        start = max(0, match_lineno - context_lines)
        end = min(len(lines), match_lineno + new_line_count + context_lines)

        parts = []
        # 截断过长的 new_string 显示
        if new_line_count > 20:
            display_end = match_lineno + 5
            for i in range(start, min(display_end, end)):
                parts.append(f"{i + 1:6d}→{lines[i]}")
            parts.append(f"       ... (共 {new_line_count} 行)")
            tail_start = match_lineno + new_line_count
            for i in range(tail_start, end):
                parts.append(f"{i + 1:6d}→{lines[i]}")
        else:
            for i in range(start, end):
                parts.append(f"{i + 1:6d}→{lines[i]}")

        return "\n".join(parts)
    except Exception:
        return ""


# 文件粒度锁，防止同路径并发写
_file_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _get_file_lock(file_path: str) -> threading.Lock:
    with _locks_mutex:
        if file_path not in _file_locks:
            _file_locks[file_path] = threading.Lock()
        return _file_locks[file_path]


@ToolRegistry.register
class ReadFileTool(BaseTool):
    """带行号读取文件，支持分页。"""

    name = "read_file"
    description = (
        "读取文件内容，每行带行号前缀。支持分页（offset/limit）。"
        "返回格式：'行号→内容'，便于精确引用。"
        """注意：本会话中已完整读取过的文件再次调用本工具时，将自动返回"已在上下文"提示。"""
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件的绝对路径或相对路径",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（从1开始），默认1",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "最多读取行数，默认2000",
                "default": 2000,
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, offset: int = 1, limit: int = 2000) -> ToolResult:
        try:
            resolved = safe_resolve(file_path)
            if not resolved.exists():
                return ToolResult(content=f"文件不存在: {file_path}", is_error=True)
            if not resolved.is_file():
                return ToolResult(content=f"路径不是文件: {file_path}", is_error=True)

            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            start = max(0, offset - 1)
            end = min(start + limit, total)

            numbered = "\n".join(
                f"{i + 1:6d}→{line}" for i, line in enumerate(lines[start:end], start=start)
            )
            suffix = f"\n[... 共 {total} 行，显示 {start+1}-{end} 行]" if end < total else ""
            return ToolResult(content=numbered + suffix)
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"读取文件失败: {e}", is_error=True)


@ToolRegistry.register
class WriteFileTool(BaseTool):
    """完整写入或创建文件，自动创建父目录。"""

    name = "write_file"
    description = (
        "将内容完整写入文件（覆盖或新建）。自动创建所需的父目录。"
        "写入前请确认用户已同意覆盖已有文件。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "目标文件的绝对或相对路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的完整文件内容",
            },
        },
        "required": ["file_path", "content"],
    }

    def execute(self, file_path: str, content: str) -> ToolResult:
        try:
            resolved = safe_resolve(file_path)
            lock = _get_file_lock(str(resolved))
            with lock:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content, encoding="utf-8")
            _auto_sync_memory(str(resolved))
            return ToolResult(content=f"已写入 {resolved}（{len(content.splitlines())} 行）")
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"写入文件失败: {e}", is_error=True)


@ToolRegistry.register
class EditFileTool(BaseTool):
    """精确字符串替换，强制唯一性检查。"""

    name = "edit_file"
    description = (
        "在文件中精确替换字符串。old_string 必须在文件中唯一出现（除非 replace_all=true）。"
        "如果 old_string 不唯一，工具会返回错误并说明出现次数，请提供更多上下文再试。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要编辑的文件路径",
            },
            "old_string": {
                "type": "string",
                "description": "要被替换的精确字符串（必须在文件中唯一出现）",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新字符串",
            },
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有出现（默认 false）",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        try:
            resolved = safe_resolve(file_path)
            if not resolved.exists():
                return ToolResult(content=f"文件不存在: {file_path}", is_error=True)

            lock = _get_file_lock(str(resolved))
            with lock:
                original = resolved.read_text(encoding="utf-8")
                count = original.count(old_string)

                if count == 0:
                    return ToolResult(
                        content=f"old_string 在文件中不存在，请检查内容是否匹配（注意空格和换行）",
                        is_error=True,
                    )
                if count > 1 and not replace_all:
                    return ToolResult(
                        content=f"old_string 不唯一（出现 {count} 次）。请提供更多上下文使其唯一，或设置 replace_all=true",
                        is_error=True,
                    )

                if replace_all:
                    modified = original.replace(old_string, new_string)
                    replaced = count
                else:
                    modified = original.replace(old_string, new_string, 1)
                    replaced = 1

                resolved.write_text(modified, encoding="utf-8")

            _auto_sync_memory(str(resolved))
            ctx = _get_edit_context(resolved, new_string)
            suffix = f"\n\n修改后上下文：\n{ctx}" if ctx else ""
            return ToolResult(content=f"已替换 {replaced} 处 in {resolved}{suffix}")
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"编辑文件失败: {e}", is_error=True)


@ToolRegistry.register
class SearchFileTool(BaseTool):
    """单文件正则搜索，返回匹配行及上下文。"""

    name = "search_in_file"
    description = "在单个文件中用正则表达式搜索，返回匹配行及前后上下文行。"
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要搜索的文件路径",
            },
            "pattern": {
                "type": "string",
                "description": "Python 正则表达式搜索模式",
            },
            "context": {
                "type": "integer",
                "description": "每个匹配前后显示的上下文行数，默认2",
                "default": 2,
            },
        },
        "required": ["file_path", "pattern"],
    }

    def execute(self, file_path: str, pattern: str, context: int = 2) -> ToolResult:
        try:
            resolved = safe_resolve(file_path)
            if not resolved.exists():
                return ToolResult(content=f"文件不存在: {file_path}", is_error=True)

            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            regex = re.compile(pattern)

            match_indices = [i for i, line in enumerate(lines) if regex.search(line)]
            if not match_indices:
                return ToolResult(content=f"未找到匹配: {pattern}")

            shown: set[int] = set()
            parts = []
            for idx in match_indices:
                start = max(0, idx - context)
                end = min(len(lines), idx + context + 1)
                if parts and start <= max(shown):
                    # 合并相邻片段
                    start = max(shown) + 1
                if start > (max(shown) + 1 if shown else 0):
                    parts.append("  ...")
                for i in range(start, end):
                    if i not in shown:
                        marker = "→" if i == idx else " "
                        parts.append(f"{i+1:6d}{marker} {lines[i]}")
                        shown.add(i)

            header = f"在 {file_path} 中找到 {len(match_indices)} 处匹配:\n"
            return ToolResult(content=header + "\n".join(parts))
        except re.error as e:
            return ToolResult(content=f"正则表达式错误: {e}", is_error=True)
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"搜索失败: {e}", is_error=True)
