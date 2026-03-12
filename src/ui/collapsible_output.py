"""可折叠输出面板：工具调用结果的实时 / 静态折叠显示。

折叠状态（默认）：
    ┌─ ✓ shell  [Ctrl+O 展开] ──────────────────────┐
    │  … 42 行已折叠                                 │
    │  line 43                                        │
    │  line 44                                        │
    │  line 45                                        │
    └─────────────────────────────────────────────────┘

展开状态（Ctrl+O 后）：
    ┌─ ✓ shell  [Ctrl+O 折叠] ──────────────────────┐
    │  line 1 ... line 45（全部内容）                │
    └─────────────────────────────────────────────────┘

用法 1 – 流式（shell 命令）：
    co = CollapsibleOutput(console, title="⚡ shell", border_style="cyan")
    with co:
        with CtrlOListener(co.toggle):
            for line in stream:
                co.feed(line)

用法 2 – 静态（普通工具结果）：
    co = CollapsibleOutput(console, title="✓ read_file 32ms", border_style="green")
    co.feed(full_content)
    co.print_static()
"""
from __future__ import annotations

import threading
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel

# 折叠时保留的尾部行数
TAIL_LINES = 3

# ── 全局"当前输出"注册（供 Ctrl+O 按键绑定访问）──────────────

_current_output: Optional["CollapsibleOutput"] = None


def set_current(co: "CollapsibleOutput") -> None:
    """注册最近一次工具输出，供全局 Ctrl+O 绑定切换。"""
    global _current_output
    _current_output = co


def get_current() -> Optional["CollapsibleOutput"]:
    """返回最近一次工具输出（可能为 None）。"""
    return _current_output


class CollapsibleOutput:
    """实时可折叠输出面板。"""

    def __init__(
        self,
        console: Console,
        title: str,
        border_style: str = "green",
        interactive: bool = False,
    ) -> None:
        """
        Args:
            console: Rich Console 对象
            title: Panel 标题（支持 Rich markup）
            border_style: 边框颜色
            interactive: True 时在标题追加 Ctrl+O 提示（用于流式模式）
        """
        self.console = console
        self.title = title
        self.border_style = border_style
        self.interactive = interactive

        self._lines: list[str] = []
        self._expanded: bool = False
        self._live: Optional[Live] = None
        self._lock = threading.Lock()
        self._last_ended_with_newline: bool = True  # 上次 feed 是否以 \n 结尾

    # ── 数据写入 ──────────────────────────────────────────

    def feed(self, text: str) -> None:
        """追加文本（支持多行），并刷新 Live（如果活跃）。

        每次 on_line 回调传入的 text 通常是一行（含尾部 \\n）。
        对于不以 \\n 结尾的片段（最后一行未结束），合并到上一行末尾。
        """
        parts = text.split("\n")
        # 末尾的空串代表文本以 \n 结尾，丢弃
        if parts and parts[-1] == "":
            parts = parts[:-1]
            ended_with_newline = True
        else:
            ended_with_newline = False

        with self._lock:
            for i, seg in enumerate(parts):
                cleaned = seg.rstrip("\r")
                is_last = i == len(parts) - 1
                if (
                    i == 0
                    and self._lines
                    and not self._last_ended_with_newline
                ):
                    # 接续上一行未完成的内容
                    self._lines[-1] += cleaned
                else:
                    self._lines.append(cleaned)
            self._last_ended_with_newline = ended_with_newline or not parts
        self._refresh_live()

    def finish(self, empty_msg: str = "(无输出)") -> None:
        """命令结束后调用，保证面板不显示「运行中」。"""
        with self._lock:
            if not self._lines:
                self._lines.append(empty_msg)
        self._refresh_live()

    def set_title(self, title: str) -> None:
        """动态更新标题（在 Live 模式下即时生效）。"""
        self.title = title
        self._refresh_live()

    def toggle(self) -> None:
        """切换展开 / 折叠状态。"""
        with self._lock:
            self._expanded = not self._expanded
        self._refresh_live()

    # ── 渲染 ──────────────────────────────────────────────

    def _refresh_live(self) -> None:
        if self._live is not None:
            self._live.update(self._build_panel())

    def _build_panel(self) -> Panel:
        with self._lock:
            lines = list(self._lines)
            expanded = self._expanded

        if not lines:
            body = "[dim]运行中...[/dim]"
        elif expanded:
            body = "\n".join(lines)
        else:
            tail = lines[-TAIL_LINES:]
            hidden = len(lines) - len(tail)
            parts: list[str] = []
            if hidden > 0:
                parts.append(f"[dim]… {hidden} 行已折叠[/dim]")
            parts.extend(tail)
            body = "\n".join(parts)

        title = self.title
        if self.interactive:
            hint = "[dim][Ctrl+O 折叠][/dim]" if expanded else "[dim][Ctrl+O 展开][/dim]"
            title = f"{title}  {hint}"

        return Panel(
            body,
            title=title,
            border_style=self.border_style,
            expand=False,
        )

    # ── Live 流式模式（context manager）──────────────────

    def __enter__(self) -> "CollapsibleOutput":
        self._live = Live(
            self._build_panel(),
            console=self.console,
            refresh_per_second=10,
            transient=False,
        )
        self._live.start(refresh=True)
        return self

    def __exit__(self, *_args) -> None:
        if self._live:
            self._live.update(self._build_panel())
            self._live.stop()
            self._live = None

    # ── 静态模式（非流式工具结果）────────────────────────

    def print_static(self) -> None:
        """直接打印折叠面板（不进入 Live 模式）。"""
        self.console.print(self._build_panel())
