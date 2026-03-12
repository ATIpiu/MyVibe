"""Ctrl+O 键盘监听器：后台线程检测 Ctrl+O（0x0f）并触发回调。

Windows：使用 msvcrt.kbhit() / msvcrt.getch() 轮询（50ms 间隔）。
Unix：使用 tty raw 模式 + select.select() 轮询（50ms 间隔）。

生命周期（context manager）：
    with CtrlOListener(callback):
        # shell 命令执行期间，监听 Ctrl+O
        ...

注意：监听器仅在工具执行期间激活，不与权限弹窗（Prompt.ask）并发，
因此不存在多线程争用 stdin 的问题。
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

CTRL_O = 0x0F  # Ctrl+O = ASCII 15


class CtrlOListener:
    """后台线程监听 Ctrl+O 按键，触发 on_toggle 回调。"""

    def __init__(self, on_toggle: Callable[[], None]) -> None:
        self._callback = on_toggle
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "CtrlOListener":
        self._stop.clear()
        target = self._poll_windows if sys.platform == "win32" else self._poll_unix
        self._thread = threading.Thread(
            target=target, daemon=True, name="ctrl-o-listener"
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None

    def __enter__(self) -> "CtrlOListener":
        return self.start()

    def __exit__(self, *_args) -> None:
        self.stop()

    # ── Windows ───────────────────────────────────────────

    def _poll_windows(self) -> None:
        try:
            import msvcrt
        except ImportError:
            return

        ctrl_o_byte = bytes([CTRL_O])
        while not self._stop.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch == ctrl_o_byte:
                        self._callback()
            except Exception:
                pass
            self._stop.wait(0.05)

    # ── Unix ─────────────────────────────────────────────

    def _poll_unix(self) -> None:
        try:
            import os
            import select
            import termios
            import tty
        except ImportError:
            return

        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
        except Exception:
            return  # 非 TTY，跳过

        ctrl_o_byte = bytes([CTRL_O])
        try:
            tty.setraw(fd)
            while not self._stop.is_set():
                try:
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        ch = os.read(fd, 1)
                        if ch == ctrl_o_byte:
                            self._callback()
                except Exception:
                    pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except Exception:
                pass
