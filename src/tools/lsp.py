"""LSP 工具：通过 pylsp JSON-RPC 提供 hover 和 goto_definition。"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .base_tool import BaseTool, ToolRegistry, ToolResult

# 模块级单例：由外部调用 set_lsp_root() 初始化
_lsp_client: Optional[LspClient] = None
_lsp_root: str = ""


def set_lsp_root(root_path: str) -> None:
    """由 main.py 在启动时注入项目根目录，触发 LSP 客户端懒初始化。"""
    global _lsp_root
    _lsp_root = root_path


def _get_client() -> Optional[LspClient]:
    """获取（或懒启动）全局 LspClient 单例。"""
    global _lsp_client, _lsp_root
    if _lsp_client is not None and _lsp_client.is_alive():
        return _lsp_client
    if not _lsp_root:
        return None
    client = LspClient()
    if client.initialize(_lsp_root):
        _lsp_client = client
        return _lsp_client
    return None


class LspClient:
    """pylsp JSON-RPC 客户端，管理进程生命周期和请求/响应匹配。"""

    def __init__(self, server_cmd: str = "pylsp"):
        self.server_cmd = server_cmd
        self._process: Optional[subprocess.Popen] = None
        self._initialized = False
        self._request_id = 0
        # id -> (threading.Event, list)  list 用于存放响应
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._opened_files: set[str] = set()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def initialize(self, root_path: str) -> bool:
        """启动 pylsp 进程并完成 LSP initialize 握手。"""
        try:
            self._process = subprocess.Popen(
                self.server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
            )
        except FileNotFoundError:
            return False

        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="lsp-reader"
        )
        self._reader_thread.start()

        root_uri = Path(root_path).resolve().as_uri()
        response = self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {},
                }
            },
        }, timeout=15.0)

        if response and "result" in response:
            self._notify("initialized", {})
            self._initialized = True
            return True
        return False

    def shutdown(self) -> None:
        """优雅关闭 LSP 服务器。"""
        if self._initialized and self._process:
            try:
                self._request("shutdown", {}, timeout=3.0)
                self._notify("exit", {})
            except Exception:
                pass
        if self._process:
            self._process.terminate()
            self._process = None
        self._initialized = False

    def is_alive(self) -> bool:
        """检查 pylsp 进程是否仍在运行。"""
        return self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------
    # JSON-RPC 底层
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send(self, message: dict) -> None:
        data = json.dumps(message).encode()
        header = f"Content-Length: {len(data)}\r\n\r\n".encode()
        assert self._process and self._process.stdin
        self._process.stdin.write(header + data)
        self._process.stdin.flush()

    def _read_one_message(self) -> Optional[dict]:
        """从 stdout 读取一条完整的 LSP 消息。"""
        assert self._process and self._process.stdout
        headers: dict[str, str] = {}
        while True:
            raw = self._process.stdout.readline()
            if not raw:
                return None
            line = raw.decode(errors="replace").strip()
            if not line:
                break
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip()] = value.strip()

        content_length = int(headers.get("Content-Length", 0))
        if content_length == 0:
            return None
        raw_body = self._process.stdout.read(content_length)
        return json.loads(raw_body.decode(errors="replace"))

    def _reader_loop(self) -> None:
        """后台线程：持续读取响应并唤醒对应的等待方。"""
        while self._process and self._process.poll() is None:
            try:
                msg = self._read_one_message()
                if msg is None:
                    break
                msg_id = msg.get("id")
                if msg_id is not None:
                    with self._lock:
                        entry = self._pending.get(msg_id)
                    if entry:
                        event, container = entry
                        container.append(msg)
                        event.set()
            except Exception:
                break

    def _request(self, method: str, params: dict, timeout: float = 10.0) -> Optional[dict]:
        """发送请求并阻塞等待响应（最多 timeout 秒）。"""
        req_id = self._next_id()
        event = threading.Event()
        container: list = []
        with self._lock:
            self._pending[req_id] = (event, container)
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            event.wait(timeout=timeout)
        finally:
            with self._lock:
                self._pending.pop(req_id, None)
        return container[0] if container else None

    def _notify(self, method: str, params: dict) -> None:
        """发送通知（无需响应）。"""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------------
    # 文件管理
    # ------------------------------------------------------------------

    def _ensure_opened(self, abs_path: str) -> None:
        """确保文件已通过 textDocument/didOpen 告知 LSP 服务器。"""
        if abs_path in self._opened_files:
            return
        try:
            with open(abs_path, encoding="utf-8") as f:
                text = f.read()
            uri = Path(abs_path).as_uri()
            self._notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            })
            self._opened_files.add(abs_path)
            time.sleep(0.5)  # 等待服务器完成解析
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 高层 API
    # ------------------------------------------------------------------

    def hover(self, file_path: str, line: int, character: int) -> Optional[str]:
        """返回指定位置的 hover 文本（类型信息 + 文档字符串）。line 从 1 开始。"""
        if not self._initialized:
            return None
        abs_path = str(Path(file_path).resolve())
        self._ensure_opened(abs_path)
        response = self._request("textDocument/hover", {
            "textDocument": {"uri": Path(abs_path).as_uri()},
            "position": {"line": line - 1, "character": character},
        })
        if not response or "result" not in response or not response["result"]:
            return None
        contents = response["result"].get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "").strip()
        if isinstance(contents, list):
            parts = []
            for c in contents:
                parts.append(c.get("value", "") if isinstance(c, dict) else str(c))
            return "\n".join(p for p in parts if p).strip()
        return str(contents).strip()

    def goto_definition(
        self, file_path: str, line: int, character: int
    ) -> Optional[dict]:
        """返回定义位置 {uri, range}。line 从 1 开始。"""
        if not self._initialized:
            return None
        abs_path = str(Path(file_path).resolve())
        self._ensure_opened(abs_path)
        response = self._request("textDocument/definition", {
            "textDocument": {"uri": Path(abs_path).as_uri()},
            "position": {"line": line - 1, "character": character},
        })
        if not response or "result" not in response or not response["result"]:
            return None
        result = response["result"]
        if isinstance(result, list):
            return result[0] if result else None
        return result


# ------------------------------------------------------------------
# Agent 工具
# ------------------------------------------------------------------

@ToolRegistry.register
class LspHoverTool(BaseTool):
    """获取指定文件位置的符号类型信息和文档注释（通过 pylsp）。"""

    name = "lsp_hover"
    description = (
        "查询某个符号（变量、函数、类）的类型签名和文档字符串。"
        "需提供文件路径、行号（从1开始）、列号（从0开始）。"
        "适合在修改代码前确认函数签名，或理解第三方库的 API。"
        "依赖 pylsp，未安装时返回提示。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径"},
            "line": {"type": "integer", "description": "行号（从1开始）"},
            "character": {"type": "integer", "description": "列号（从0开始）"},
        },
        "required": ["file_path", "line", "character"],
    }

    def execute(self, file_path: str, line: int, character: int) -> ToolResult:
        client = _get_client()
        if client is None:
            return ToolResult(
                content=(
                    "LSP 客户端未就绪。请确认已安装 pylsp（pip install python-lsp-server）"
                    "且项目根目录已正确设置。"
                ),
                is_error=True,
            )
        result = client.hover(file_path, line, character)
        if result is None:
            return ToolResult(
                content=f"未获取到 {file_path}:{line}:{character} 的 hover 信息（该位置可能没有符号）。",
                is_error=False,
            )
        return ToolResult(content=result)


@ToolRegistry.register
class LspDefinitionTool(BaseTool):
    """跳转到符号的定义位置（通过 pylsp）。"""

    name = "lsp_definition"
    description = (
        "查找变量、函数或类的定义所在文件和行号。"
        "返回定义位置的文件路径和行列范围。"
        "适合追踪函数实现来源，尤其是跨文件引用。"
        "依赖 pylsp，未安装时返回提示。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "当前文件路径"},
            "line": {"type": "integer", "description": "行号（从1开始）"},
            "character": {"type": "integer", "description": "列号（从0开始）"},
        },
        "required": ["file_path", "line", "character"],
    }

    def execute(self, file_path: str, line: int, character: int) -> ToolResult:
        client = _get_client()
        if client is None:
            return ToolResult(
                content=(
                    "LSP 客户端未就绪。请确认已安装 pylsp（pip install python-lsp-server）"
                    "且项目根目录已正确设置。"
                ),
                is_error=True,
            )
        result = client.goto_definition(file_path, line, character)
        if result is None:
            return ToolResult(
                content=f"未找到 {file_path}:{line}:{character} 处符号的定义。",
                is_error=False,
            )
        # 将 URI 转回可读路径
        uri = result.get("uri", "")
        if uri.startswith("file:///"):
            from urllib.request import url2pathname
            path = url2pathname(uri[7:])
        else:
            path = uri
        r = result.get("range", {})
        start = r.get("start", {})
        def_line = start.get("line", 0) + 1
        def_char = start.get("character", 0)
        return ToolResult(content=f"{path}:{def_line}:{def_char}")
