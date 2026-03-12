"""结构化日志：Rich 控制台输出（不再写 JSONL 文件，完整内容由 .agent_sessions JSONL 保存）。"""
import time
import traceback
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from .log_formats import LogEvent

_loggers: dict[str, "StructuredLogger"] = {}


class StructuredLogger:
    """控制台日志记录器：仅输出到 Rich 控制台。"""

    def __init__(
        self,
        session_id: str,
        log_dir: str = "logs",
        level: str = "INFO",
        console_colors: bool = True,
    ) -> None:
        self.session_id = session_id
        self.level = level
        self.console = Console(highlight=False, markup=True) if console_colors else Console(highlight=False, markup=False, no_color=True)
        self._turn = 0

    # ── 核心（仅控制台，无文件写入）─────────────────────────────────────

    def log(self, event: str, data: dict, level: str = "INFO") -> None:
        """空实现：文件日志已移除，完整内容由 .agent_sessions JSONL 记录。"""

    def set_turn(self, turn: int) -> None:
        self._turn = turn

    def turn_start(self, turn: int, user_input: str) -> None:
        """记录轮次开始（不打印 panel，避免重复显示用户输入）。"""
        self.log("turn_start", {"turn": turn, "user_input": user_input[:500]})

    def turn_end(self, turn: int, iterations: int, total_input: int, total_output: int, total_cached: int, total_reasoning: int, total_cost: float) -> None:
        """打印轮次结束汇总（含思考链 tokens）。"""
        hit_rate = total_cached / total_input if total_input > 0 else 0.0
        grand_total = total_input + total_output + total_reasoning
        self.log("turn_end", {
            "turn": turn, "iterations": iterations,
            "total_input": total_input, "total_output": total_output,
            "total_reasoning": total_reasoning, "total_cached": total_cached,
            "grand_total": grand_total, "total_cost": total_cost,
        })
        think_str = f" | 思考 {total_reasoning:,}" if total_reasoning > 0 else ""
        self.console.print(
            f"[dim]─ Turn {turn} 完毕 | 迭代 {iterations} 次 | "
            f"输入 {total_input:,} | 输出 {total_output:,}{think_str} | "
            f"实际消耗 [bold]{grand_total:,}[/bold] | "
            f"缓存命中 {total_cached:,}({hit_rate:.0%}) | ¥{total_cost:.4f} ─[/dim]"
        )

    # ── 工具调用 ──────────────────────────────────────────────────────────

    def tool_call(self, tool_name: str, args: dict, tool_use_id: str) -> None:
        self.log(LogEvent.TOOL_CALL, {"tool_name": tool_name, "tool_use_id": tool_use_id, "args": args})
        args_text = "\n".join(f"  [dim]{k}[/dim]: {str(v)[:200]}" for k, v in args.items())
        panel = Panel(
            args_text or "[dim](无参数)[/dim]",
            title=f"[bold cyan]Tool: {tool_name}[/bold cyan]",
            border_style="cyan",
            expand=False,
        )
        self.console.print(panel)

    def tool_result(
        self,
        tool_name: str,
        tool_use_id: str,
        success: bool,
        summary: str,
        elapsed_ms: int,
        full_content: str = "",
        pre_rendered: bool = False,
    ) -> None:
        """渲染工具结果。

        Args:
            pre_rendered: 为 True 时跳过渲染（已由 CollapsibleOutput 流式显示过，
                          如 shell 工具）。仍会记录结构化日志。
        """
        self.log(
            LogEvent.TOOL_RESULT,
            {"tool_name": tool_name, "tool_use_id": tool_use_id, "success": success, "summary": summary, "elapsed_ms": elapsed_ms},
        )
        if pre_rendered:
            return

        from ..ui.collapsible_output import CollapsibleOutput

        icon = "✓" if success else "✗"
        color = "green" if success else "red"
        display_content = full_content or summary
        if len(display_content) > 3000:
            display_content = (
                display_content[:3000]
                + f"\n[dim]… 输出过长，仅显示前 3000 字符[/dim]"
            )

        co = CollapsibleOutput(
            self.console,
            title=f"[bold {color}]{icon} {tool_name}[/bold {color}] [dim]{elapsed_ms}ms[/dim]",
            border_style=color,
            interactive=False,
        )
        co.feed(display_content or "(空结果)")
        co.print_static()

        # 注册为"当前输出"，供 Ctrl+O 全局快捷键访问
        from ..ui.collapsible_output import set_current
        set_current(co)

    def tool_error(self, tool_name: str, tool_use_id: str, error: Exception) -> None:
        tb = traceback.format_exc()
        self.log(LogEvent.TOOL_ERROR, {"tool_name": tool_name, "tool_use_id": tool_use_id, "error": str(error), "traceback": tb}, level="ERROR")
        self.console.print(f"  └─ [[bold red]✗ {tool_name}[/bold red]] [red]{error}[/red]")

    # ── LLM 交互 ──────────────────────────────────────────────────────────

    def llm_request(
        self,
        model: str,
        messages_count: int,
        estimated_tokens: int,
        tools_count: int,
        max_tokens: int = 200000,
    ) -> None:
        ratio = estimated_tokens / max_tokens * 100 if max_tokens else 0
        self.log(LogEvent.LLM_REQUEST, {"model": model, "messages_count": messages_count, "estimated_tokens": estimated_tokens, "tools_count": tools_count})

    def llm_stream_start(self, model: str) -> None:
        self.log(LogEvent.LLM_STREAM_START, {"model": model})
        self.console.print("[magenta]▶ LLM 响应中...[/magenta]")

    def llm_stream_token(self, delta: str, cumulative_chars: int) -> None:
        if self.level == "DEBUG":
            self.log(LogEvent.LLM_STREAM_TOKEN, {"delta": delta, "cumulative_chars": cumulative_chars}, level="DEBUG")

    def llm_stream_tool(self, tool_name: str, tool_use_id: str) -> None:
        self.log(LogEvent.LLM_STREAM_TOOL, {"tool_name": tool_name, "tool_use_id": tool_use_id})
        self.console.print(f"\n[bold cyan]→ 调用工具: {tool_name}[/bold cyan]")

    def llm_response(
        self,
        stop_reason: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        elapsed_ms: int,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> None:
        total_tokens = input_tokens + output_tokens + reasoning_tokens
        self.log(
            LogEvent.LLM_RESPONSE,
            {
                "stop_reason": stop_reason,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "elapsed_ms": elapsed_ms,
            },
        )
        hit_rate = cached_tokens / input_tokens if input_tokens > 0 else 0.0
        cache_str = (
            f"[green]缓存 {cached_tokens:,}({hit_rate:.0%})[/green]"
            if cached_tokens > 0
            else "[dim]缓存 0[/dim]"
        )
        think_str = f" | [yellow]思考 {reasoning_tokens:,}[/yellow]" if reasoning_tokens > 0 else ""
        self.console.print(
            f"  └─ [magenta][LLM][/magenta] stop={stop_reason} | "
            f"输入 {input_tokens:,} | 输出 {output_tokens:,}{think_str} | "
            f"合计 [bold]{total_tokens:,}[/bold] | {cache_str} | ¥{cost_usd:.4f} | {elapsed_ms}ms"
        )

    # ── 上下文 / 权限 ─────────────────────────────────────────────────────

    def context_ratio(self, ratio: float, token_count: int, max_tokens: int) -> None:
        self.log(LogEvent.CONTEXT_RATIO, {"ratio": ratio, "token_count": token_count, "max_tokens": max_tokens}, level="WARNING")
        pct = ratio * 100
        self.console.print(
            f"[bold yellow]⚠[/bold yellow]  上下文已用 {pct:.1f}%"
            f"（{token_count:,} / {max_tokens:,} tokens）建议执行 /compact"
        )

    def permission_check(self, tool_name: str, action: str, granted: bool) -> None:
        self.log(LogEvent.PERMISSION_CHECK, {"tool_name": tool_name, "action": action, "granted": granted})
        status = "[bold green]已允许[/bold green]" if granted else "[bold red]已拒绝[/bold red]"
        self.console.print(f"  [dim][权限][/dim] {tool_name} ({action}) → {status}")

    def summary_cache(self, file_path: str, hit: bool, reason: str = "") -> None:
        self.log(LogEvent.SUMMARY_CACHE, {"file_path": file_path, "hit": hit, "reason": reason})

def get_logger(session_id: str = "default") -> StructuredLogger:
    """获取或创建模块级单例日志记录器。"""
    if session_id not in _loggers:
        _loggers[session_id] = StructuredLogger(session_id)
    return _loggers[session_id]
