"""文件操作工具：读取、写入、编辑、搜索、glob、grep。"""
from __future__ import annotations

import glob as _glob
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult
from ..utils.path import safe_resolve


def _auto_sync_index(file_path: str) -> None:
    """写入/编辑成功后同步代码索引（仅对 .py 文件）。"""
    if not file_path.endswith(".py"):
        return
    try:
        from .index.tools import get_injected_manager
        mgr = get_injected_manager()
        result = mgr.sync(file_path)
        count = result.get("functions_count", 0)
        if count is not None:
            print(f"  [index] 同步 {file_path}: {count} 个函数", flush=True)
    except Exception as e:
        print(f"  [index] 同步失败 {file_path}: {e}", flush=True)

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
            _auto_sync_index(str(resolved))
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

            _auto_sync_index(str(resolved))
            ctx = _get_edit_context(resolved, new_string)
            suffix = f"\n\n修改后上下文：\n{ctx}" if ctx else ""
            return ToolResult(content=f"已替换 {replaced} 处 in {resolved}{suffix}")
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)
        except Exception as e:
            return ToolResult(content=f"编辑文件失败: {e}", is_error=True)


@ToolRegistry.register
class GlobFilesTool(BaseTool):
    """快速文件名模式匹配，按修改时间倒序返回路径列表。"""

    name = "glob_files"
    description = (
        "快速文件名模式匹配（glob），按修改时间倒序返回匹配路径列表。\n\n"
        "## 工具使用优先级链\n"
        "1. read_file(scope='overview') → 了解项目模块结构\n"
        "2. glob_files（本工具）→ 按名称模式定位文件\n"
        "3. grep_files → 按内容定位具体位置\n\n"
        "## 适用场景\n"
        "- 查找特定模式的文件：**/*.py、src/**/*.ts\n"
        "- 确认文件是否存在\n"
        "- 获取目录下的文件列表\n\n"
        "## 不适用场景\n"
        "- 搜索文件内容 → 用 grep_files\n"
        "不要用 shell find/ls 替代此工具。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式，如 **/*.py、src/**/*.ts、*.json",
            },
            "path": {
                "type": "string",
                "description": "搜索根目录，默认为 agent 工作目录",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: Optional[str] = None) -> ToolResult:
        try:
            base = Path(path) if path else safe_resolve(".")
            if not base.exists():
                return ToolResult(content=f"目录不存在: {path}", is_error=True)

            # 使用 pathlib rglob 或 glob.glob 递归匹配
            if "**" in pattern:
                matches = list(base.glob(pattern))
            else:
                matches = list(base.glob(pattern)) or list(base.rglob(pattern))

            # 过滤只保留文件，按 mtime 倒序
            files = [p for p in matches if p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            if not files:
                return ToolResult(content=f"未找到匹配 '{pattern}' 的文件")

            lines = [f"找到 {len(files)} 个文件（按修改时间倒序）:"]
            for p in files:
                lines.append(str(p))
            return ToolResult(content="\n".join(lines))
        except Exception as e:
            return ToolResult(content=f"glob 搜索失败: {e}", is_error=True)


@ToolRegistry.register
class GrepFilesTool(BaseTool):
    """跨文件正则内容搜索，基于 ripgrep 或 Python re 回退。"""

    name = "grep_files"
    description = (
        "正则内容搜索（基于 ripgrep 或 Python re 回退）。支持跨文件和单文件（path 指向具体文件）。\n\n"
        "## 工具使用优先级链\n"
        "1. read_file(scope='overview'/'file'/'function') → 了解项目模块/函数结构\n"
        "2. grep_files（本工具）→ 定位到具体位置（文件+行号）\n\n"
        "## 输出模式（output_mode）\n"
        "- files_with_matches（默认）：只返回匹配的文件路径，适合快速定位\n"
        "- content：返回匹配行及上下文，适合直接查看代码\n"
        "- count：返回每文件匹配数量\n\n"
        "单文件搜索：path 直接传文件路径即可，无需其他工具。\n"
        "不要用 shell grep/rg 替代此工具。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "正则表达式搜索模式",
            },
            "path": {
                "type": "string",
                "description": "搜索根目录，默认为 agent 工作目录",
            },
            "glob": {
                "type": "string",
                "description": "文件过滤 glob 模式，如 *.py、**/*.ts",
            },
            "output_mode": {
                "type": "string",
                "enum": ["files_with_matches", "content", "count"],
                "description": "输出模式，默认 files_with_matches",
                "default": "files_with_matches",
            },
            "context": {
                "type": "integer",
                "description": "output_mode=content 时每个匹配前后的上下文行数，默认 2",
                "default": 2,
            },
            "-A": {
                "type": "integer",
                "description": "每个匹配后显示的行数",
            },
            "-B": {
                "type": "integer",
                "description": "每个匹配前显示的行数",
            },
            "-i": {
                "type": "boolean",
                "description": "大小写不敏感，默认 false",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "限制输出条数，防止海量输出，默认不限制",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: Optional[str] = None, **kwargs) -> ToolResult:
        glob_filter = kwargs.get("glob")
        output_mode = kwargs.get("output_mode", "files_with_matches")
        context = kwargs.get("context", 2)
        after = kwargs.get("-A", context)
        before = kwargs.get("-B", context)
        case_insensitive = kwargs.get("-i", False)
        head_limit = kwargs.get("head_limit")

        base_path = Path(path) if path else safe_resolve(".")
        base = str(base_path)

        # 单文件路径直接走 Python re 精准搜索；rg 在 `-l`/`--glob` 组合下对单文件行为
        # 与目录不一致（会因 .gitignore 或搜索根判定导致假阴性），不适合作为单文件搜索。
        if base_path.exists() and base_path.is_file():
            return self._single_file_grep(
                pattern, base_path, output_mode, after, before,
                case_insensitive, head_limit,
            )

        # 优先尝试 ripgrep
        result = self._try_rg(
            pattern, base, glob_filter, output_mode, after, before,
            case_insensitive, head_limit,
        )
        if result is not None:
            return result

        # 降级到 Python re
        return self._python_grep(
            pattern, base, glob_filter, output_mode, after, before,
            case_insensitive, head_limit,
        )

    def _single_file_grep(
        self, pattern, file_path: Path, output_mode, after, before,
        case_insensitive, head_limit,
    ) -> ToolResult:
        """对单个文件直接用 Python re 搜索，绕过 rg 对单文件的不一致行为。"""
        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(content=f"正则表达式错误: {e}", is_error=True)

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(content=f"读取文件失败: {e}", is_error=True)

        lines = text.splitlines()
        match_indices = [i for i, ln in enumerate(lines) if regex.search(ln)]

        rel = str(file_path)
        if not match_indices:
            return ToolResult(
                content=(
                    f"未找到匹配 '{pattern}'（已在单文件 {rel} 中搜索 {len(lines)} 行）。\n"
                    "若确信应有匹配：检查正则转义是否正确；或去掉 path 参数改做全局搜索。"
                )
            )

        if output_mode == "files_with_matches":
            return ToolResult(content=rel)
        if output_mode == "count":
            return ToolResult(content=f"{rel}: {len(match_indices)}")

        # content 模式：输出带上下文
        out: list[str] = []
        shown: set[int] = set()
        for idx in match_indices:
            start = max(0, idx - before)
            end = min(len(lines), idx + after + 1)
            if out:
                out.append("--")
            for i in range(start, end):
                if i in shown:
                    continue
                marker = ":" if i == idx else "-"
                out.append(f"{rel}:{i + 1}{marker}{lines[i]}")
                shown.add(i)
        if head_limit:
            out = out[:head_limit]
        return ToolResult(content="\n".join(out))

    def _try_rg(
        self, pattern, base, glob_filter, output_mode, after, before,
        case_insensitive, head_limit,
    ) -> Optional[ToolResult]:
        try:
            cmd = ["rg", "--no-heading"]
            if case_insensitive:
                cmd.append("-i")
            if glob_filter:
                cmd += ["--glob", glob_filter]

            if output_mode == "files_with_matches":
                cmd.append("-l")
            elif output_mode == "count":
                cmd.append("-c")
            else:  # content
                cmd += [f"-A{after}", f"-B{before}"]

            cmd += [pattern, base]

            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, encoding="utf-8",
                errors="replace",
            )
            if proc.returncode not in (0, 1):
                return None  # rg 不存在或出错，降级

            output = proc.stdout.strip()
            if not output:
                return ToolResult(
                    content=(
                        f"未找到匹配: {pattern}（搜索根: {base}）\n"
                        "建议：1) 检查正则转义；2) 用更短关键词；"
                        "3) 去掉 path/glob 限制；4) 试 read_file(scope='overview')"
                    )
                )

            lines = output.splitlines()
            if head_limit:
                lines = lines[:head_limit]
            return ToolResult(content="\n".join(lines))
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            return None

    def _python_grep(
        self, pattern, base, glob_filter, output_mode, after, before,
        case_insensitive, head_limit,
    ) -> ToolResult:
        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)

            # 收集候选文件
            base_path = Path(base)
            if glob_filter:
                candidates = list(base_path.rglob(glob_filter))
            else:
                candidates = list(base_path.rglob("*"))
            candidates = [p for p in candidates if p.is_file()]

            results_files: list[str] = []
            results_content: list[str] = []
            count_map: dict[str, int] = {}

            for file_path in candidates:
                try:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                file_lines = text.splitlines()
                match_indices = [i for i, ln in enumerate(file_lines) if regex.search(ln)]
                if not match_indices:
                    continue

                rel = str(file_path)
                if output_mode == "files_with_matches":
                    results_files.append(rel)
                elif output_mode == "count":
                    count_map[rel] = len(match_indices)
                else:
                    shown: set[int] = set()
                    for idx in match_indices:
                        start = max(0, idx - before)
                        end = min(len(file_lines), idx + after + 1)
                        if results_content:
                            results_content.append("--")
                        for i in range(start, end):
                            if i not in shown:
                                marker = ":" if i == idx else "-"
                                results_content.append(f"{rel}:{i+1}{marker}{file_lines[i]}")
                                shown.add(i)

            if output_mode == "files_with_matches":
                if not results_files:
                    return ToolResult(content=f"未找到匹配: {pattern}")
                if head_limit:
                    results_files = results_files[:head_limit]
                return ToolResult(content="\n".join(results_files))
            elif output_mode == "count":
                if not count_map:
                    return ToolResult(content=f"未找到匹配: {pattern}")
                lines = [f"{f}: {c}" for f, c in count_map.items()]
                if head_limit:
                    lines = lines[:head_limit]
                return ToolResult(content="\n".join(lines))
            else:
                if not results_content:
                    return ToolResult(content=f"未找到匹配: {pattern}")
                if head_limit:
                    results_content = results_content[:head_limit]
                return ToolResult(content="\n".join(results_content))
        except re.error as e:
            return ToolResult(content=f"正则表达式错误: {e}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"grep 搜索失败: {e}", is_error=True)
