"""Git 操作工具：状态、差异、提交，以及对话轮次版本管理。"""
from __future__ import annotations

import subprocess
from typing import Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult


def run_git(args: list[str], cwd: Optional[str] = None) -> tuple[str, str, int]:
    """封装 subprocess git 调用。

    Returns:
        (stdout, stderr, returncode) 三元组
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=30,
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", "git 命令未找到，请确认已安装 git", 127
    except subprocess.TimeoutExpired:
        return "", "git 命令超时", 1
    except Exception as e:
        return "", str(e), 1


# ─────────────────── 对话轮次版本管理（内部 API） ───────────────────

TURN_PREFIX = "[turn-"  # commit message 前缀标记

# agent 内部目录：日志/缓存/记忆，不应纳入项目 git 追踪
_AGENT_IGNORE_DIRS = ["logs/", ".agent_sessions/", ".agent_cache/", ".vibecoding/"]


def _ensure_agent_gitignore(cwd: str) -> None:
    """确保 .gitignore 包含 agent 内部目录，并取消追踪已提交的这些目录。

    这样 git reset --hard 就不会因为日志文件被锁定而失败。
    """
    from pathlib import Path as _Path
    gitignore_path = _Path(cwd) / ".gitignore"
    existing = ""
    if gitignore_path.exists():
        try:
            existing = gitignore_path.read_text(encoding="utf-8")
        except OSError:
            return

    missing = [d for d in _AGENT_IGNORE_DIRS if d not in existing]
    if missing:
        addition = "\n# AI Coding Agent 内部目录（自动添加）\n" + "\n".join(missing) + "\n"
        try:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write(addition)
        except OSError:
            return
        # 取消追踪已纳入 git 的 agent 目录
        for d in missing:
            run_git(["rm", "-r", "--cached", "--ignore-unmatch", d.rstrip("/")], cwd=cwd)


def auto_commit_turn(cwd: str, turn: int, user_input: str, session_id: str = "") -> Optional[str]:
    """每轮对话结束后自动 git add -A && git commit。

    commit message 格式：[turn-N|sess-<session_id>] <用户输入前100字符>
    使用 --allow-empty，即使无文件变化也记录轮次。
    若 cwd 不是 git 仓库则静默跳过，返回 None。
    """
    _, _, code = run_git(["rev-parse", "--git-dir"], cwd=cwd)
    if code != 0:
        return None

    # 确保 agent 目录被 gitignore（防止日志文件锁导致 reset --hard 失败）
    _ensure_agent_gitignore(cwd)
    run_git(["add", "-A"], cwd=cwd)

    msg_preview = user_input.replace("\n", " ")[:100]
    sess_tag = f"|sess-{session_id}" if session_id else ""
    commit_msg = f"[turn-{turn}{sess_tag}] {msg_preview}"

    _, _, commit_code = run_git(
        ["commit", "--allow-empty", "-m", commit_msg],
        cwd=cwd,
    )
    if commit_code != 0:
        return None

    hash_out, _, _ = run_git(["rev-parse", "--short", "HEAD"], cwd=cwd)
    return hash_out.strip() or None


def get_turn_history(cwd: str, session_id: str = "") -> list[dict]:
    """读取当前 git 仓库中属于指定会话的 turn commit，从小到大排序。

    每项格式：{"turn": int, "hash": str(7位), "full_hash": str, "message": str, "date": str}
    session_id 为空时返回所有会话的记录。
    """
    stdout, _, code = run_git(
        ["log", "--format=%H|||%s|||%ad", "--date=short"],
        cwd=cwd,
    )
    if code != 0:
        return []

    results: list[dict] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|||", 2)
        if len(parts) != 3:
            continue
        full_hash, subject, date = parts
        if not subject.startswith(TURN_PREFIX):
            continue
        try:
            close_bracket = subject.index("]")
            inner = subject[len(TURN_PREFIX):close_bracket]  # "3" 或 "3|sess-abc123"
            user_msg = subject[close_bracket + 2:]           # "] " 之后的内容

            if "|sess-" in inner:
                turn_str, sess_id = inner.split("|sess-", 1)
            else:
                turn_str, sess_id = inner, ""

            turn_num = int(turn_str)

            # 会话过滤
            if session_id and sess_id != session_id:
                continue

            results.append({
                "turn": turn_num,
                "session_id": sess_id,
                "hash": full_hash[:7],
                "full_hash": full_hash,
                "message": user_msg,
                "date": date,
            })
        except (ValueError, IndexError):
            continue

    return sorted(results, key=lambda x: x["turn"])


def revert_to_turn(cwd: str, turn: int, session_id: str = "") -> tuple[bool, str]:
    """使用 git reset --hard 回退到指定 turn 的文件状态。

    Returns:
        (success, commit_hash_short)
    """
    history = get_turn_history(cwd, session_id=session_id)
    target = next((h for h in history if h["turn"] == turn), None)
    if target is None:
        available = [str(h["turn"]) for h in history]
        return False, f"未找到 turn {turn}。可用轮次：{', '.join(available) or '无'}"

    _, stderr, code = run_git(["reset", "--hard", target["full_hash"]], cwd=cwd)
    if code != 0:
        return False, f"git reset --hard 失败: {stderr}"

    return True, target["hash"]


@ToolRegistry.register
class GitStatusTool(BaseTool):
    """显示 git 工作区状态。"""

    name = "git_status"
    description = "显示 git 仓库工作区和暂存区状态（等同于 git status --short）"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "git 仓库路径，默认为当前目录",
                "default": ".",
            },
        },
    }

    def execute(self, path: str = ".") -> ToolResult:
        stdout, stderr, code = run_git(["status", "--short", "--branch"], cwd=path)
        if code != 0:
            return ToolResult(content=f"git status 失败: {stderr}", is_error=True)
        return ToolResult(content=stdout or "(工作区干净)")


@ToolRegistry.register
class GitDiffTool(BaseTool):
    """显示 git 差异（暂存区或工作区）。"""

    name = "git_diff"
    description = "显示 git 差异。staged=true 显示已暂存改动，否则显示工作区未暂存改动。"
    input_schema = {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "true 显示已暂存改动（git diff --staged），false 显示工作区改动",
                "default": False,
            },
            "file": {
                "type": "string",
                "description": "限定到特定文件（可选）",
            },
            "path": {
                "type": "string",
                "description": "git 仓库路径",
                "default": ".",
            },
        },
    }

    def execute(
        self,
        staged: bool = False,
        file: Optional[str] = None,
        path: str = ".",
    ) -> ToolResult:
        args = ["diff"]
        if staged:
            args.append("--staged")
        if file:
            args += ["--", file]

        stdout, stderr, code = run_git(args, cwd=path)
        if code != 0:
            return ToolResult(content=f"git diff 失败: {stderr}", is_error=True)

        if not stdout:
            return ToolResult(content="(无差异)")

        # 截断超长输出
        if len(stdout) > 20000:
            stdout = stdout[:20000] + "\n... [diff 已截断]"
        return ToolResult(content=stdout)


@ToolRegistry.register
class GitCommitTool(BaseTool):
    """暂存指定文件并创建 git 提交。"""

    name = "git_commit"
    description = (
        "将指定文件暂存（git add）并创建提交（git commit）。"
        "此操作需要用户权限确认。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "提交消息",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提交的文件路径列表（相对于仓库根目录）",
            },
            "path": {
                "type": "string",
                "description": "git 仓库路径",
                "default": ".",
            },
        },
        "required": ["message", "files"],
    }

    def execute(
        self,
        message: str,
        files: list[str],
        path: str = ".",
    ) -> ToolResult:
        if not files:
            return ToolResult(content="files 列表不能为空", is_error=True)

        # git add
        add_stdout, add_stderr, add_code = run_git(["add"] + files, cwd=path)
        if add_code != 0:
            return ToolResult(content=f"git add 失败: {add_stderr}", is_error=True)

        # git commit
        commit_stdout, commit_stderr, commit_code = run_git(["commit", "-m", message], cwd=path)
        if commit_code != 0:
            return ToolResult(content=f"git commit 失败: {commit_stderr}", is_error=True)

        return ToolResult(content=commit_stdout or f"已提交: {message}")
