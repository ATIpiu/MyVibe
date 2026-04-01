"""Shell 执行工具：安全执行命令，含注入检测和权限检查。"""
from __future__ import annotations

import re
import subprocess
import threading
import sys
from typing import Callable, Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult


def _sanitize_surrogates(s: str) -> str:
    """移除字符串中的孤立代理字符（lone surrogates）。

    Windows 下某些 emoji 或特殊字节被错误解码后会产生 U+D800-U+DFFF 范围内的
    孤立代理字符，Python 的 UTF-8 编码器不接受这类字符，写入文件/JSON 时会崩溃。
    用 replace 模式重新编解码可将其替换为 U+FFFD（替换字符）。
    """
    return s.encode("utf-8", errors="replace").decode("utf-8")


def _decode_output(data: bytes) -> str:
    """解码命令输出字节流，依次尝试 UTF-8 / GBK / latin-1，并净化孤立代理字符。

    Windows cmd 默认使用 GBK（CP936），直接以 utf-8 解码会导致乱码；
    依序尝试多种编码并 fallback 到 latin-1（永不失败）确保输出可读。
    """
    if not data:
        return ""
    candidates = ["utf-8", "gbk", "latin-1"]
    for enc in candidates:
        try:
            return _sanitize_surrogates(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return _sanitize_surrogates(data.decode("utf-8", errors="replace"))

# 危险命令默认模式（可由配置覆盖）
DEFAULT_DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r":\(\)\s*\{",           # fork bomb
    r"dd\s+if=/dev/zero",
    r"mkfs",
    r">\s*/dev/sd[a-z]",
    r"chmod\s+-R\s+777\s+/",
    r"wget.*\|\s*bash",
    r"curl.*\|\s*sh",
]

ALWAYS_DENY_PATTERNS = [
    r"rm\s+-rf\s+/\s*$",
    r":\(\)\s*\{.*:\|:&\s*\}",
]


def check_shell_injection(command: str) -> bool:
    """检测命令是否包含 Shell 注入特征（反引号、$()、管道到危险命令）。

    Returns:
        True 表示存在注入风险
    """
    injection_patterns = [
        r"`[^`]+`",           # 反引号命令替换
        r"\$\([^)]+\)",       # $() 命令替换（嵌套）
    ]
    return any(re.search(p, command) for p in injection_patterns)


def classify_command_danger(
    command: str,
    dangerous_patterns: Optional[list[str]] = None,
) -> str:
    """对命令进行危险等级分类。

    Returns:
        "safe" | "confirm" | "deny"
    """
    patterns = dangerous_patterns or DEFAULT_DANGEROUS_PATTERNS

    for p in ALWAYS_DENY_PATTERNS:
        if re.search(p, command):
            return "deny"

    for p in patterns:
        if re.search(p, command, re.IGNORECASE):
            return "confirm"

    # 写操作需要确认
    write_ops = [r"\brm\b", r"\bmv\b", r"\bcp\b.*-r", r"\bchmod\b", r"\bchown\b"]
    for p in write_ops:
        if re.search(p, command):
            return "confirm"

    return "safe"


@ToolRegistry.register
class ShellTool(BaseTool):
    """在 shell 中执行命令，含安全检查和超时控制。"""

    name = "shell"
    description = (
        "在系统 shell 中执行命令。会进行注入检测和危险命令检查。"
        "危险命令需要用户确认。输出超过 10000 字符时自动截断。\n\n"
        "重要：不要用 shell 做以下操作（有专用工具更安全高效）：\n"
        "- 文件搜索 → glob_files（不要 find/ls）\n"
        "- 内容搜索 → grep_files（不要 grep/rg）\n"
        "- 文件读取 → read_file（不要 cat/head/tail）\n"
        "- 文件编辑 → edit_file（不要 sed/awk）"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时毫秒数，默认 120000（2分钟）",
                "default": 120000,
            },
            "description": {
                "type": "string",
                "description": "命令的人类可读描述（用于权限确认弹窗）",
                "default": "",
            },
            "working_dir": {
                "type": "string",
                "description": "命令执行的工作目录（默认为 agent cwd）",
            },
        },
        "required": ["command"],
    }

    def __init__(self, dangerous_patterns: Optional[list[str]] = None, cwd: Optional[str] = None):
        self.dangerous_patterns = dangerous_patterns or DEFAULT_DANGEROUS_PATTERNS
        self.cwd = cwd

    def execute(
        self,
        command: str,
        timeout: int = 120000,
        description: str = "",
        working_dir: Optional[str] = None,
    ) -> ToolResult:
        # 1. Shell 注入检测
        if check_shell_injection(command):
            return ToolResult(
                content="命令包含 Shell 注入特征（反引号或 $() 嵌套），已拒绝执行",
                is_error=True,
            )

        # 2. 危险等级分类
        danger = classify_command_danger(command, self.dangerous_patterns)
        if danger == "deny":
            return ToolResult(content=f"命令被永久拒绝执行（高危操作）: {command}", is_error=True)

        # 3. 执行命令（以字节模式捕获，随后多编码解码防止乱码）
        timeout_sec = timeout / 1000.0
        cwd = working_dir or self.cwd

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=False,          # 字节模式，手动解码
                timeout=timeout_sec,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(content=f"命令超时（{timeout_sec:.0f}秒）: {command}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"命令执行异常: {e}", is_error=True)

        # 4. 解码输出（Windows GBK / Unix UTF-8 自动适配）
        MAX_OUTPUT = 10000
        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)
        combined = stdout
        if stderr:
            combined += f"\n[stderr]\n{stderr}"

        truncated = False
        if len(combined) > MAX_OUTPUT:
            combined = combined[:MAX_OUTPUT] + f"\n... [输出已截断，原始长度 {len(combined)} 字符]"
            truncated = True

        if result.returncode != 0:
            return ToolResult(
                content=f"[退出码 {result.returncode}]\n{combined}",
                is_error=True,
            )

        return ToolResult(content=combined or "(无输出)")

    def execute_stream(
        self,
        command: str,
        on_line: Callable[[str], None],
        timeout: int = 120000,
        working_dir: Optional[str] = None,
    ) -> ToolResult:
        """执行命令并实时回调每一行输出（用于 CollapsibleOutput 流式显示）。

        Args:
            command: Shell 命令
            on_line: 每次产生一行（含换行符）时的回调
            timeout: 超时毫秒数
            working_dir: 工作目录
        """
        if check_shell_injection(command):
            return ToolResult(
                content="命令包含 Shell 注入特征（反引号或 $() 嵌套），已拒绝执行",
                is_error=True,
            )

        danger = classify_command_danger(command, self.dangerous_patterns)
        if danger == "deny":
            return ToolResult(
                content=f"命令被永久拒绝执行（高危操作）: {command}",
                is_error=True,
            )

        timeout_sec = timeout / 1000.0
        cwd = working_dir or self.cwd

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=subprocess.DEVNULL,   # 禁止子进程读取控制台，避免抢占 Ctrl+O 按键
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
        except Exception as e:
            return ToolResult(content=f"命令启动异常: {e}", is_error=True)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _read(stream: object, dest: list[str], prefix: str) -> None:
            for raw in iter(stream.readline, b""):  # type: ignore[attr-defined]
                line = _decode_output(raw).rstrip("\r\n")
                dest.append(line)
                on_line(prefix + line + "\n")
            stream.close()  # type: ignore[attr-defined]

        t_out = threading.Thread(
            target=_read, args=(proc.stdout, stdout_lines, ""), daemon=True
        )
        t_err = threading.Thread(
            target=_read, args=(proc.stderr, stderr_lines, "[stderr] "), daemon=True
        )
        t_out.start()
        t_err.start()

        timed_out = False
        t_out.join(timeout=timeout_sec)
        if t_out.is_alive():
            proc.kill()
            timed_out = True
        t_err.join(timeout=0.5)
        try:
            rc = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            rc = -1

        if timed_out:
            return ToolResult(
                content=f"命令超时（{timeout_sec:.0f}秒）: {command}",
                is_error=True,
            )

        MAX_OUTPUT = 10000
        combined = "\n".join(stdout_lines)
        if stderr_lines:
            combined += "\n[stderr]\n" + "\n".join(stderr_lines)

        if len(combined) > MAX_OUTPUT:
            combined = (
                combined[:MAX_OUTPUT]
                + f"\n... [输出已截断，原始长度 {len(combined)} 字符]"
            )

        if rc != 0:
            return ToolResult(
                content=f"[退出码 {rc}]\n{combined}",
                is_error=True,
            )

        return ToolResult(content=combined or "(无输出)")
