"""AI Coding Agent - CLI 入口。"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# 确保 src 包可被导入
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.coding_agent import CodingAgent, PermissionManager
from src.agent.state import AgentState, SessionManager
from src.context.context_manager import ContextManager
from src.llm.client import create_client_from_config
from src.logger.structured_logger import StructuredLogger
from src.utils.path import get_project_root

# 触发工具注册（导入即注册）
import src.tools.file          # noqa: F401
import src.tools.shell         # noqa: F401
import src.tools.git           # noqa: F401
import src.tools.lsp           # noqa: F401
import src.tools.context_tools # noqa: F401
import src.tools.memory_tools  # noqa: F401

from src.memory.memory_manager import get_memory_manager
from src.tools.memory_tools import set_memory_manager
from src.tools.lsp import set_lsp_root

console = Console()


def parse_args() -> Namespace:
    """解析 CLI 参数。"""
    parser = ArgumentParser(
        prog="agent",
        description="AI Coding Agent - 本地 AI 编程助手",
    )
    parser.add_argument(
        "-p", "--print",
        metavar="PROMPT",
        help="一次性执行 prompt 后退出（headless 模式）",
    )
    parser.add_argument(
        "--model",
        help="覆盖配置中的模型名称",
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="继续最近一次会话",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="恢复指定 session_id 的会话",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="配置文件路径（默认 config/config.yaml）",
    )
    parser.add_argument(
        "--cwd",
        help="指定工作目录（默认为当前目录）",
    )
    return parser.parse_args()


def load_config(config_path: str = "config/config.yaml") -> dict:
    """加载 YAML 配置，支持环境变量覆盖。"""
    path = Path(config_path)

    # 查找配置文件（相对于 main.py 所在目录）
    if not path.exists():
        alt_path = Path(__file__).parent.parent / config_path
        if alt_path.exists():
            path = alt_path

    config: dict = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            console.print(f"[yellow]警告: 配置文件加载失败: {e}，使用默认配置[/yellow]")

    # 环境变量覆盖（支持智谱 GLM 和 Anthropic 两套 key）
    for env_key in ("ZHIPU_API_KEY", "GLM_API_KEY", "ANTHROPIC_API_KEY"):
        if env_key in os.environ:
            config.setdefault("llm", {})["api_key"] = os.environ[env_key]
            break
    if "AGENT_MODEL" in os.environ:
        config.setdefault("llm", {})["model"] = os.environ["AGENT_MODEL"]

    return config


def handle_slash_command(
    command: str,
    agent: CodingAgent,
    session_manager: SessionManager,
    cwd: str = ".",
) -> bool:
    """处理斜杠命令。

    Returns:
        True 表示命令已处理（不需要发给 LLM）
    """
    parts = command.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/exit" or cmd == "/quit":
        console.print("[dim]再见！[/dim]")
        sys.exit(0)

    elif cmd == "/clear":
        agent.state.messages.clear()
        agent.state.turn = 0
        console.print("[green]对话历史已清除[/green]")
        return True

    elif cmd == "/compact":
        console.print("[blue]正在压缩对话历史...[/blue]")
        agent.state = session_manager.compress_history(
            agent.state, agent.llm, keep_recent=10
        )
        session_manager.save(agent.state)
        console.print(f"[green]压缩完成，保留 {len(agent.state.messages)} 条消息[/green]")
        return True

    elif cmd == "/cost":
        s = agent.state
        llm = agent.llm
        table = Table(title="Token 使用统计", show_header=True, header_style="bold cyan")
        table.add_column("指标", style="cyan", min_width=20)
        table.add_column("数值", justify="right")
        # 会话累计（含子 Agent，从 LLMClient 读取）
        table.add_row("[bold]本次运行合计[/bold]", "")
        table.add_row("  输入 tokens", f"{llm.session_input_tokens:,}")
        table.add_row("  输出 tokens", f"{llm.session_output_tokens:,}")
        if llm.session_reasoning_tokens:
            table.add_row("  思考链 tokens", f"{llm.session_reasoning_tokens:,}")
        table.add_row("  费用 (CNY)", f"¥{llm.session_cost_usd:.4f}")
        table.add_row("", "")
        # 主 Agent 历史累计（持久化，从 state 读取）
        table.add_row("[bold]主 Agent 历史累计[/bold]", "")
        table.add_row("  输入 tokens", f"{s.total_input_tokens:,}")
        table.add_row("  输出 tokens", f"{s.total_output_tokens:,}")
        if s.total_reasoning_tokens:
            table.add_row("  思考链 tokens", f"{s.total_reasoning_tokens:,}")
        table.add_row("  费用 (CNY)", f"¥{s.total_cost_usd:.4f}")
        table.add_row("  对话轮数", str(s.turn))
        console.print(table)
        return True

    elif cmd == "/sessions":
        sessions = session_manager.list_sessions()
        if not sessions:
            console.print("[dim]暂无会话记录[/dim]")
            return True
        table = Table(title="历史会话", show_header=True)
        table.add_column("Session ID", style="cyan")
        table.add_column("轮数", justify="right")
        table.add_column("消息数", justify="right")
        table.add_column("费用", justify="right")
        table.add_column("修改时间")
        for s in sessions[:20]:
            table.add_row(
                s["session_id"],
                str(s["turn"]),
                str(s["messages"]),
                f"${s['cost_usd']:.4f}",
                s["modified"],
            )
        console.print(table)
        return True

    elif cmd == "/history":
        from src.tools.git import get_turn_history
        session_id = agent.state.session_id
        history = get_turn_history(cwd, session_id=session_id)
        if not history:
            console.print("[dim]本会话暂无轮次提交记录（工作目录可能不是 git 仓库，或尚未完成过对话轮次）[/dim]")
            return True
        table = Table(title=f"对话轮次历史（会话 {session_id}）", show_header=True)
        table.add_column("Turn", style="cyan", justify="right")
        table.add_column("Hash", style="dim")
        table.add_column("日期", style="dim")
        table.add_column("用户输入")
        for h in history:
            table.add_row(str(h["turn"]), h["hash"], h["date"], h["message"])
        console.print(table)
        console.print("[dim]使用 /revert 交互式选择回退轮次[/dim]")
        return True

    elif cmd == "/revert":
        from src.tools.git import get_turn_history, revert_to_turn
        from rich.prompt import Prompt

        session_id = agent.state.session_id
        history = get_turn_history(cwd, session_id=session_id)
        if not history:
            console.print("[dim]本会话暂无轮次提交记录[/dim]")
            return True

        # 展示历史表格供选择
        table = Table(title=f"选择要回退的轮次（会话 {session_id}）", show_header=True)
        table.add_column("Turn", style="cyan", justify="right")
        table.add_column("Hash", style="dim")
        table.add_column("日期", style="dim")
        table.add_column("用户输入")
        for h in history:
            table.add_row(str(h["turn"]), h["hash"], h["date"], h["message"])
        console.print(table)

        valid_turns = [str(h["turn"]) for h in history]
        try:
            choice = Prompt.ask(
                f"输入要回退的 Turn 编号（{', '.join(valid_turns)}），q 取消",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]已取消[/dim]")
            return True

        if choice.lower() == "q":
            console.print("[dim]已取消[/dim]")
            return True

        try:
            target_turn = int(choice)
        except ValueError:
            console.print(f"[red]无效输入：{choice}[/red]")
            return True

        target = next((h for h in history if h["turn"] == target_turn), None)
        if target is None:
            console.print(f"[red]未找到 turn {target_turn}[/red]")
            return True

        console.print(Panel(
            f"[bold]Turn {target_turn}[/bold]  ({target['date']}  {target['hash']})\n"
            f"用户输入：{target['message']}",
            title="[bold yellow]准备回退[/bold yellow]",
            border_style="yellow",
            expand=False,
        ))
        console.print("[bold red]警告：将回退文件状态和对话上下文，该轮次之后的改动将丢失！[/bold red]")

        try:
            confirm = Prompt.ask("确认回退？", choices=["y", "n"], default="n").lower()
        except (EOFError, KeyboardInterrupt):
            confirm = "n"

        if confirm != "y":
            console.print("[dim]已取消[/dim]")
            return True

        # 1. 回退文件状态
        success, msg = revert_to_turn(cwd, target_turn, session_id=session_id)
        if not success:
            console.print(f"[bold red]文件回退失败：{msg}[/bold red]")
            return True

        console.print(f"[green]文件已回退到 turn {target_turn}（commit {msg}）[/green]")

        # 2. 回退对话上下文
        restored = session_manager.load_at_turn(session_id, target_turn)
        if restored:
            agent.state = restored
            console.print(f"[green]对话上下文已回退到 turn {target_turn}（共 {len(restored.messages)} 条消息）[/green]")
        else:
            console.print("[yellow]警告：未能从会话文件恢复上下文，对话历史未改变。可手动执行 /clear[/yellow]")

        return True

    elif cmd == "/init":
        from src.agent.project_init import initialize_project
        console.print("[dim]正在初始化项目记忆（MyVibe.md）...[/dim]")
        did_generate = initialize_project(agent.llm, agent._memory_manager, cwd, console)
        if not did_generate:
            console.print("[dim]MyVibe.md 已存在，跳过生成。如需重新生成请先删除该文件。[/dim]")
        display_memory_stats(console, agent._memory_manager)
        return True

    elif cmd == "/context":
        _show_context(console, agent, cwd)
        return True

    elif cmd == "/plan":
        agent.state.plan_mode = not agent.state.plan_mode
        status = "开启" if agent.state.plan_mode else "关闭"
        console.print(f"[bold green]计划模式已{status}[/bold green]")
        return True

    elif cmd == "/help":
        help_text = (
            "[bold]可用斜杠命令：[/bold]\n"
            "  /init          - 扫描项目并用 LLM 生成 MyVibe.md 项目记忆\n"
            "  /context       - 查看上下文用量、系统提示词、MyVibe.md 及记忆统计\n"
            "  /clear         - 清除当前对话历史\n"
            "  /compact       - 压缩对话历史（节省 tokens）\n"
            "  /cost          - 显示 token 使用量和费用\n"
            "  /sessions      - 列出所有历史会话\n"
            "  /history       - 查看对话轮次 git 提交历史\n"
            "  /revert        - 列出历史轮次并交互选择回退目标\n"
            "  /plan          - 切换计划模式（Ctrl+P）\n"
            "  /help          - 显示此帮助\n"
            "  /exit          - 退出程序"
        )
        console.print(Panel(help_text, title="帮助", border_style="blue"))
        return True

    return False


def _show_context(console: Console, agent, cwd: str) -> None:
    """显示当前上下文概览：额度用量 / 系统提示词 / MyVibe.md / 记忆统计。"""
    import json
    from rich.table import Table
    from rich.rule import Rule
    from src.agent.project_init import load_myvibe

    state = agent.state
    MAX_CTX = 200_000

    # ── 1. 上下文额度 ────────────────────────────────────────────
    used = state.last_response_input_tokens or 0
    pct = used / MAX_CTX if MAX_CTX else 0
    bar_width = 36
    filled = int(bar_width * pct)
    if pct < 0.6:
        bar_color = "green"
    elif pct < 0.85:
        bar_color = "yellow"
    else:
        bar_color = "red"
    bar = f"[{bar_color}]{'█' * filled}[/{bar_color}][dim]{'░' * (bar_width - filled)}[/dim]"

    console.print(Rule("[bold cyan]上下文概览[/bold cyan]", style="cyan"))
    console.print(f"  {bar}  [{bar_color}]{used:,}[/{bar_color}][dim] / {MAX_CTX // 1000}K tokens  ({pct:.1%})[/dim]")
    console.print()

    # ── 2. 上下文组成明细 ────────────────────────────────────────
    sys_prompt = state.system_prompt or ""
    sys_tokens = len(sys_prompt) // 4
    msg_chars = sum(
        len(json.dumps(m.get("content", ""), ensure_ascii=False))
        for m in state.messages
    )
    msg_tokens = msg_chars // 4

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim", width=16)
    table.add_column()
    table.add_column(justify="right", style="cyan")
    table.add_row("系统提示词",  f"[dim]含工具描述、规则、主动记忆注入等[/dim]",  f"~{sys_tokens:,} tokens")
    table.add_row("对话历史",    f"[dim]{len(state.messages)} 条消息（用户/助手/工具结果）[/dim]", f"~{msg_tokens:,} tokens")
    console.print(table)
    console.print()

    # ── 3. 系统提示词预览 ────────────────────────────────────────
    if sys_prompt:
        preview = sys_prompt[:600] + ("\n[dim]… 仅展示前 600 字符[/dim]" if len(sys_prompt) > 600 else "")
        console.print(Panel(
            preview,
            title=f"[bold]系统提示词[/bold]  [dim]{len(sys_prompt):,} 字符 / ~{sys_tokens:,} tokens[/dim]",
            border_style="dim",
            expand=False,
        ))
    else:
        console.print("[dim]  系统提示词：尚未生成（第一轮对话后可见）[/dim]")
    console.print()

    # ── 4. MyVibe.md ─────────────────────────────────────────────
    myvibe = load_myvibe(cwd)
    if myvibe:
        mv_tokens = len(myvibe) // 4
        mv_preview = myvibe[:400] + ("\n[dim]… 仅展示前 400 字符[/dim]" if len(myvibe) > 400 else "")
        console.print(Panel(
            mv_preview,
            title=f"[bold]MyVibe.md[/bold]  [dim]{len(myvibe):,} 字符 / ~{mv_tokens:,} tokens[/dim]",
            border_style="blue",
            expand=False,
        ))
    else:
        console.print(Panel(
            "[dim]未找到 MyVibe.md，运行 /init 可自动生成[/dim]",
            title="[bold]MyVibe.md[/bold]",
            border_style="dim",
            expand=False,
        ))
    console.print()

    # ── 5. 记忆系统统计 ──────────────────────────────────────────
    try:
        all_memory = agent._memory_manager.read_all()
        total_modules = len(all_memory)
        total_funcs = sum(len(m.functions) for m in all_memory.values())
        mem_json = json.dumps(
            {k: v.to_dict() for k, v in all_memory.items()}, ensure_ascii=False
        )
        total_mem_tokens = len(mem_json) // 4
    except Exception:
        total_modules = total_funcs = total_mem_tokens = 0

    mem_tool_calls = state.memory_tool_calls
    mem_tool_tokens = state.memory_tool_tokens

    mem_table = Table(show_header=False, box=None, padding=(0, 2))
    mem_table.add_column(style="dim", width=20)
    mem_table.add_column()
    mem_table.add_column(justify="right", style="cyan")
    mem_table.add_row(
        "记忆索引总量",
        f"[dim]{total_modules} 个模块 · {total_funcs} 个函数[/dim]",
        f"~{total_mem_tokens:,} tokens",
    )
    mem_table.add_row(
        "  [dim]⚠ 注意[/dim]",
        "[dim]记忆索引不会全量注入，每轮仅注入与请求最相关的 top-8 条[/dim]",
        "",
    )
    mem_table.add_row(
        "本会话 read_memory",
        f"[dim]主动调用 {mem_tool_calls} 次，结果写入对话历史[/dim]",
        f"~{mem_tool_tokens:,} tokens" if mem_tool_calls > 0 else "[dim]0[/dim]",
    )
    console.print(Panel(
        mem_table,
        title="[bold]记忆系统[/bold]",
        border_style="magenta",
        expand=False,
    ))


def display_welcome(
    console: Console,
    session_id: str,
    model: str,
    project_root: str = "",
    memory_dir: str = "",
    tools_count: int = 0,
    max_context: int = 200_000,
) -> None:
    """显示启动 Banner。"""
    lines = [
        f"[bold cyan]MyVibe[/bold cyan]  [dim]v0.1.0[/dim]",
        f"[dim]会话 ID:[/dim]  {session_id}",
        f"[dim]模  型:[/dim]  {model}   "
        f"[dim]上下文上限:[/dim] {max_context // 1000}K tokens",
    ]
    if tools_count:
        lines.append(f"[dim]工具数:[/dim]   {tools_count} 个可用工具（文件/Shell/Git/LSP/记忆等）")
    if project_root:
        lines.append(f"[dim]项目目录:[/dim] {project_root}")
    lines.append("[dim]输入 /help 查看命令  ·  Ctrl+P 切换计划模式  ·  Ctrl+C 中断[/dim]")
    console.print(Panel("\n".join(lines), border_style="cyan", expand=False))


def sync_and_display_memory(console: Console, memory_manager) -> None:
    """后台线程启动 AST 扫描，不阻塞交互循环，完成后打印统计。"""
    import json

    def _sync():
        try:
            memory_manager.sync()
            all_memory = memory_manager.read_all()
            total_modules = len(all_memory)
            total_functions = sum(len(m.functions) for m in all_memory.values())
            json_str = json.dumps(
                {k: v.to_dict() for k, v in all_memory.items()},
                ensure_ascii=False,
            )
            est_tokens = len(json_str) // 4
            console.print(
                f"[dim]记忆索引：{total_modules} 个模块，{total_functions} 个函数，"
                f"约 {est_tokens:,} tokens[/dim]"
            )
        except Exception as e:
            console.print(f"[dim]记忆扫描失败：{e}[/dim]")

    t = threading.Thread(target=_sync, daemon=True, name="memory-sync")
    t.start()


def display_memory_stats(console: Console, memory_manager) -> None:
    """仅读取现有记忆索引并输出统计（不 sync，供 /init 完成后复用）。"""
    import json
    try:
        all_memory = memory_manager.read_all()
        total_modules = len(all_memory)
        total_functions = sum(len(m.functions) for m in all_memory.values())
        if total_modules == 0:
            console.print("[dim]记忆索引：暂无数据[/dim]")
            return
        json_str = json.dumps(
            {k: v.to_dict() for k, v in all_memory.items()},
            ensure_ascii=False,
        )
        est_tokens = len(json_str) // 4
        console.print(
            f"[dim]记忆索引：{total_modules} 个模块，{total_functions} 个函数，"
            f"约 {est_tokens:,} tokens[/dim]"
        )
    except Exception:
        pass


def run_interactive_loop(agent: CodingAgent, session_manager: SessionManager, cwd: str = ".") -> None:
    """主交互循环。"""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.key_binding import KeyBindings
        from src.completer.multi_completer import MultiCompleter

        history_file = Path(".agent_sessions") / ".prompt_history"
        history_file.parent.mkdir(exist_ok=True)
        completer = MultiCompleter(cwd)

        def get_prompt():
            if agent.state.plan_mode:
                return HTML('<ansigreen>[计划]</ansigreen> <b>[你]</b> ')
            return HTML('<b>[你]</b> ')

        def get_toolbar():
            """底部状态栏：显示模型、轮次、累计费用（含子 Agent）。"""
            model = agent.llm.model
            turn = agent.state.turn
            cost = agent.llm.session_cost_usd
            in_tok = agent.llm.session_input_tokens
            plan = " · <ansigreen>[计划模式]</ansigreen>" if agent.state.plan_mode else ""
            return HTML(
                f" <b>{model}</b> · Turn {turn} · "
                f"{in_tok:,} tokens · ¥{cost:.4f}{plan}"
            )

        kb = KeyBindings()

        @kb.add("c-p")
        def toggle_plan_mode(event):
            agent.state.plan_mode = not agent.state.plan_mode
            status = "开启" if agent.state.plan_mode else "关闭"
            console.print(f"[bold green]计划模式已{status}[/bold green]")
            event.app.invalidate()

        prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=completer,
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
            key_bindings=kb,
            bottom_toolbar=get_toolbar,
        )

        while True:
            try:
                user_input = prompt_session.prompt(get_prompt).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]再见！[/dim]")
                break

            if not user_input:
                continue

            # 处理斜杠命令
            if user_input.startswith("/"):
                if handle_slash_command(user_input, agent, session_manager, cwd):
                    continue

            # 运行 Agent（后台线程 + 主线程轮询，保证 Ctrl+C 即刻响应）
            console.print()
            agent._cancel.clear()
            _exc: list[Exception] = []

            def _run():
                try:
                    agent.run_turn(user_input)
                except Exception as e:
                    _exc.append(e)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            try:
                while t.is_alive():
                    t.join(timeout=0.05)
            except KeyboardInterrupt:
                agent._cancel.set()
                console.print("\n[yellow]已中断[/yellow]")
                t.join(timeout=2.0)
            else:
                if _exc:
                    console.print(f"\n[bold red]错误: {_exc[0]}[/bold red]")
            print()  # 确保换行

    except ImportError:
        # prompt_toolkit 未安装，退回简单 input
        while True:
            try:
                user_input = input("\n[你] ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.startswith("/"):
                if handle_slash_command(user_input, agent, session_manager):
                    continue

            try:
                agent.run_turn(user_input)
                print()
            except KeyboardInterrupt:
                console.print("\n[yellow]已中断[/yellow]")
            except Exception as e:
                console.print(f"\n[bold red]错误: {e}[/bold red]")

    # 退出前等待命名线程写入完成（最多 8 秒，避免显示"未命名"）
    nt = getattr(agent, "_naming_thread", None)
    if nt and nt.is_alive():
        console.print("[dim]正在保存会话名称...[/dim]", end="\r")
        nt.join(timeout=8.0)


def pick_session(console: Console, session_manager: SessionManager, cwd: str) -> Optional[str]:
    """启动时展示当前目录的历史会话，让用户选择是否恢复。返回 session_id 或 None。"""
    from rich.table import Table
    from rich.prompt import Prompt

    # 只展示与当前 cwd 匹配的会话
    all_sessions = session_manager.list_sessions()
    sessions = [s for s in all_sessions if s.get("cwd") == cwd]
    if not sessions:
        return None

    table = Table(title="本目录历史会话", show_header=True, header_style="bold cyan", box=None)
    table.add_column("#", style="bold", width=3, justify="right")
    table.add_column("名称", min_width=14)
    table.add_column("ID", style="dim", width=10)
    table.add_column("轮次", justify="right", width=5)
    table.add_column("费用", justify="right", width=9)
    table.add_column("时间", style="dim", width=16)

    for i, s in enumerate(sessions[:10], 1):
        name = s.get("name") or "[dim]未命名[/dim]"
        table.add_row(
            str(i),
            name,
            s["session_id"],
            str(s["turn"]),
            f"¥{s['cost_usd']:.4f}",
            s["modified"],
        )

    console.print(table)

    try:
        choice = Prompt.ask(
            "[dim]输入编号恢复会话，直接回车新建[/dim]",
            default="",
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice:
        return None
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["session_id"]
    except ValueError:
        pass
    return None


def run_headless(agent: CodingAgent, prompt: str) -> int:
    """-p 一次性执行，返回 exit code。"""
    try:
        result = agent.run_turn(prompt)
        print()
        return 0
    except Exception as e:
        console.print(f"[bold red]执行失败: {e}[/bold red]")
        return 1


def main() -> None:
    """程序入口：组装所有组件并启动。"""
    args = parse_args()

    # 加载配置
    config = load_config(args.config)

    # 模型覆盖
    if args.model:
        config.setdefault("llm", {})["model"] = args.model

    # 确定工作目录：project_root 始终为当前运行目录（cwd），
    # 不向上查找 git 根，确保 .vibecoding / .agent_cache 等缓存
    # 始终创建在用户执行命令的目录下。
    cwd = args.cwd or os.getcwd()
    project_root = cwd

    # 生成或恢复 session_id
    sessions_dir = config.get("session", {}).get("sessions_dir", ".agent_sessions")
    session_manager = SessionManager(sessions_dir)

    state: Optional[AgentState] = None
    session_id: Optional[str] = None

    # 没有显式指定 --resume / --continue 时，交互式选择历史会话
    if not args.resume and not args.continue_session and not args.print:
        chosen = pick_session(console, session_manager, cwd)
        if chosen:
            args.resume = chosen

    if args.resume:
        session_id = args.resume
        state = session_manager.load(session_id)
        if state:
            console.print(f"[green]已恢复会话: {session_id}[/green]")
        else:
            console.print(f"[yellow]未找到会话 {session_id}，创建新会话[/yellow]")

    elif args.continue_session:
        sessions = session_manager.list_sessions()
        if sessions:
            session_id = sessions[0]["session_id"]
            state = session_manager.load(session_id)
            if state:
                console.print(f"[green]继续上次会话: {session_id}[/green]")

    if state is None:
        session_id = str(uuid.uuid4())[:8]
        state = AgentState(session_id=session_id, cwd=cwd)

    # 初始化各组件
    logger = StructuredLogger(
        session_id=session_id,
        log_dir=config.get("logging", {}).get("log_dir", "logs"),
        level=config.get("logging", {}).get("level", "INFO"),
        console_colors=config.get("logging", {}).get("console_colors", True),
    )

    llm_client = create_client_from_config(config.get("llm", {}))

    cache_dir = config.get("session", {}).get("cache_dir", ".agent_cache")
    context_manager = ContextManager(project_root, cache_dir)

    perm_config = config.get("permissions", {})
    permission_manager = PermissionManager(
        auto_allow=set(perm_config.get("auto_allow", [])) or None,
        require_confirm=set(perm_config.get("require_confirm", [])) or None,
        deny=set(perm_config.get("deny", [])) or None,
        console=console,
    )

    # 初始化记忆管理器
    memory_manager = get_memory_manager(project_root)
    set_memory_manager(memory_manager)

    # 初始化 LSP 客户端（懒启动，首次调用工具时才真正连接）
    set_lsp_root(project_root)

    agent = CodingAgent(
        llm_client=llm_client,
        state=state,
        session_manager=session_manager,
        permission_manager=permission_manager,
        context_manager=context_manager,
        logger=logger,
        console=console,
        config=config,
    )

    model_name = config.get("llm", {}).get("model", "claude-sonnet-4-6")
    tools_count = len(agent._tools_schema)

    # 执行模式分支
    if args.print:
        display_welcome(console, session_id, model_name, project_root, tools_count=tools_count)
        sync_and_display_memory(console, memory_manager)
        exit_code = run_headless(agent, args.print)
        sys.exit(exit_code)
    else:
        display_welcome(console, session_id, model_name, project_root, tools_count=tools_count)
        sync_and_display_memory(console, memory_manager)
        run_interactive_loop(agent, session_manager, cwd)


if __name__ == "__main__":
    main()
