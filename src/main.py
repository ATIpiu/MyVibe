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
from src.agent.plan_agent import PlanAgent, PLAN_MODE_READONLY_TOOLS
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
import src.tools.context_tools  # noqa: F401
import src.tools.compile_tool   # noqa: F401
import src.tools.ask_user_tool  # noqa: F401
import src.tools.index.tools   # noqa: F401  代码索引工具（read_file / rebuild_index / find_symbol）

from src.tools.index.manager import get_index_manager
from src.tools.index.tools import set_index_manager
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
    parser.add_argument(
        "--super",
        dest="super_mode",
        action="store_true",
        help="Super 模式：所有工具操作无需确认",
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
    _pending_box: "Optional[list]" = None,
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
        from src.agent.project_init import get_init_prompt
        already_exists, init_prompt = get_init_prompt(cwd)
        if already_exists:
            console.print("[dim]MyVibe.md 已存在，跳过生成。如需重新生成请先删除该文件。[/dim]")
        elif _pending_box is not None:
            # 把 init prompt 推入 pending_box，由主循环在后台线程执行，避免主线程死锁
            console.print("[bold cyan]正在初始化项目记忆，Agent 将主动探索项目结构...[/bold cyan]")
            _pending_box.append(init_prompt)
        display_memory_stats(console, agent._index_manager)
        return True

    elif cmd == "/context":
        _show_context(console, agent, cwd)
        return True

    elif cmd == "/plan":
        agent.state.plan_mode = not agent.state.plan_mode
        return True

    elif cmd == "/super":
        enabled = agent.permission.toggle_super()
        if enabled:
            console.print(Panel(
                "[bold red]⚡ Super 模式已开启[/bold red]\n"
                "[dim]所有工具操作将无需确认，包括文件写入、Shell 命令、Git 提交等。[/dim]",
                border_style="red",
                expand=False,
            ))
        else:
            console.print("[green]Super 模式已关闭，恢复正常权限确认。[/green]")
        return True

    elif cmd == "/tasks":
        from src.tasks.task_manager import get_task_manager
        manager = get_task_manager()
        console.print(manager.format_list())
        return True

    elif cmd == "/task":
        from src.tasks.task_manager import get_task_manager
        manager = get_task_manager()
        if not arg:
            console.print("[yellow]用法：/task <task_id>[/yellow]")
        else:
            console.print(manager.format_detail(arg.strip()))
        return True

    elif cmd == "/bg":
        # 后台执行一个独立子代理任务
        if not arg:
            console.print("[yellow]用法：/bg <任务描述>[/yellow]")
            return True
        from src.tasks.task_manager import get_task_manager
        from src.agent.sub_agent import SubAgent
        manager = get_task_manager()

        def _bg_run():
            sub = SubAgent(agent.llm, agent._tools_schema)
            return sub.run(task=arg)

        task = manager.submit(name=f"bg: {arg[:40]}", func=_bg_run, description=arg)
        console.print(f"[green]后台任务已提交，ID: {task.id}[/green]  使用 /task {task.id} 查看结果")
        return True

    elif cmd == "/skills":
        # 列出所有已加载的 Skills
        from src.skills.skill_registry import get_registry
        registry = get_registry()
        skills = registry.all_skills()
        if not skills:
            console.print("[dim]暂无已加载的 Skills。将 .md 文件放到 ~/.myvibe/skills/ 目录即可。[/dim]")
        else:
            table = Table(title=f"已加载 Skills（{len(skills)} 个）", show_header=True)
            table.add_column("名称", style="cyan")
            table.add_column("描述")
            table.add_column("触发词", style="dim")
            for s in skills:
                table.add_row(
                    f"/{s.name}",
                    s.description,
                    ", ".join(s.triggers[:3]),
                )
            console.print(table)
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
            "  /super         - 切换 Super 模式（所有操作无需确认）\n"
            "  Ctrl+O         - 展开/折叠最近一次工具输出（执行中实时切换，结束后重新打印）\n"
            "  /skills        - 列出所有已加载的 Skills\n"
            "  /<skill-name>  - 调用指定 Skill（如 /commit、/review）\n"
            "  /help          - 显示此帮助\n"
            "  /exit          - 退出程序"
        )
        console.print(Panel(help_text, title="帮助", border_style="blue"))
        return True

    # Skill 命令：动态匹配已注册的 Skill
    cmd_name = cmd.lstrip("/")
    if cmd_name:
        try:
            from src.skills.skill_registry import get_registry
            registry = get_registry()
            skill = registry.get(cmd_name)
            if skill:
                expanded = skill.render(arg)
                console.print(f"[dim]→ 展开 Skill [{skill.name}]: {expanded[:60]}...[/dim]"
                               if len(expanded) > 60 else
                               f"[dim]→ 展开 Skill [{skill.name}]: {expanded}[/dim]")
                if _pending_box is not None:
                    _pending_box.append(expanded)
                return True
        except Exception:
            pass

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
        all_memory = agent._index_manager.read_all()
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
        "本会话 read_file",
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
    lines.append("[dim]输入 /help 查看命令  ·  Ctrl+P 计划模式  ·  Ctrl+O 展开/折叠输出  ·  Ctrl+C 中断[/dim]")
    console.print(Panel("\n".join(lines), border_style="cyan", expand=False))


def sync_and_display_memory(console: Console, index_manager) -> None:
    """后台线程启动 AST 扫描，不阻塞交互循环，完成后打印统计。"""
    import json

    def _sync():
        try:
            index_manager.sync()
            all_data = index_manager.read_all()
            total_modules = len(all_data)
            total_functions = sum(len(m.functions) for m in all_data.values())
            json_str = json.dumps(
                {k: v.to_dict() for k, v in all_data.items()},
                ensure_ascii=False,
            )
            est_tokens = len(json_str) // 4
            console.print(
                f"[dim]代码索引：{total_modules} 个模块，{total_functions} 个函数，"
                f"约 {est_tokens:,} tokens[/dim]"
            )
        except Exception as e:
            console.print(f"[dim]代码索引扫描失败：{e}[/dim]")

    t = threading.Thread(target=_sync, daemon=True, name="index-sync")
    t.start()


def display_memory_stats(console: Console, index_manager) -> None:
    """仅读取现有代码索引并输出统计（不 sync，供 /init 完成后复用）。"""
    import json
    try:
        all_data = index_manager.read_all()
        total_modules = len(all_data)
        total_functions = sum(len(m.functions) for m in all_data.values())
        if total_modules == 0:
            console.print("[dim]代码索引：暂无数据[/dim]")
            return
        json_str = json.dumps(
            {k: v.to_dict() for k, v in all_data.items()},
            ensure_ascii=False,
        )
        est_tokens = len(json_str) // 4
        console.print(
            f"[dim]代码索引：{total_modules} 个模块，{total_functions} 个函数，"
            f"约 {est_tokens:,} tokens[/dim]"
        )
    except Exception:
        pass


# ─────────────────── ask_user 工具 UI 实现 ────────────────────

def _ask_user_interactive(
    question: str,
    options: list,
    allow_custom: bool,
    prompt_session,
    console: Console,
) -> "Optional[str]":
    """ask_user 工具的 UI 实现：展示问题，收集用户回答。

    - 有 options：显示数字选择菜单（最后一项为自定义输入，若 allow_custom=True）
    - 无 options：纯文本输入框

    Returns:
        用户回答字符串，或 None 表示取消。
    """
    console.print(f"\n[bold cyan]❓ {question}[/bold cyan]")

    if options:
        # 补充"自定义"选项
        if allow_custom and not any(kw in (options[-1] if options else "") for kw in ("自定义", "custom", "其他")):
            options = list(options) + ["其他 / 自定义输入"]

        n = len(options)
        for j, opt in enumerate(options, 1):
            if j == n and allow_custom:
                console.print(f"  [dim]{j}. {opt}[/dim]")
            else:
                console.print(f"  [bold]{j}.[/bold] {opt}")

        idx = _keypress_select(n, prompt_session)
        if idx is None:
            return None

        if allow_custom and idx == n - 1:
            # 自定义输入
            try:
                from prompt_toolkit.formatted_text import HTML
                if prompt_session is not None:
                    custom = prompt_session.prompt(
                        HTML("<ansiyellow>  请输入自定义内容：</ansiyellow> ")
                    ).strip()
                else:
                    custom = input("  请输入自定义内容：").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if custom:
                console.print(f"[green]  ✓ 自定义：{custom}[/green]")
                return custom
            return None
        else:
            chosen = options[idx]
            console.print(f"[green]  ✓ 已选择：{chosen}[/green]")
            return chosen
    else:
        # 纯文本输入
        try:
            from prompt_toolkit.formatted_text import HTML
            if prompt_session is not None:
                answer = prompt_session.prompt(
                    HTML("<ansiyellow>  请输入回答：</ansiyellow> ")
                ).strip()
            else:
                answer = input("  请输入回答：").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer:
            console.print(f"[green]  ✓ 已输入：{answer}[/green]")
        return answer or None


# ─────────────────── 计划模式问题解析与交互 ────────────────────

def _has_plan_questions(text: str) -> bool:
    """检测计划文本中是否含有待确认问题块。"""
    import re
    return bool(re.search(r'\*\*问题\s*\d*\s*[：:]', text))


def _parse_plan_questions(text: str) -> "list[tuple[str, list[str]]]":
    """从计划文本解析问题标题和选项列表。

    Returns:
        [(question_label, [opt1, opt2, ...]), ...]
    """
    import re
    result = []
    # 按 **问题 N：...** 分割
    pattern = r'\*\*问题\s*(\d*)\s*[：:]\s*([^*]+?)\*\*'
    headers = [(m.group(0), m.group(1), m.group(2).strip(), m.start())
               for m in re.finditer(pattern, text)]
    if not headers:
        return result

    for i, (full_match, num, q_text, start) in enumerate(headers):
        end = headers[i + 1][3] if i + 1 < len(headers) else len(text)
        block = text[start:end]
        # 提取数字编号选项
        options = [m.group(1).strip()
                   for m in re.finditer(r'^\d+\.\s+(.+)$', block, re.MULTILINE)
                   if m.group(1).strip()]
        if not options:
            continue
        label = f"问题 {num}：{q_text}" if num else f"问题：{q_text}"
        result.append((label, options))
    return result


def _keypress_select(
    n: int,
    prompt_session,
    hint: str = "",
) -> "Optional[int]":
    """按下数字键 1-n 立即返回对应下标（0-based），无需 Enter。

    利用 prompt_session 的自定义 key_bindings：按数字键时直接将
    该字符写入 buffer 并调用 validate_and_handle()（相当于按 Enter）。
    Returns None 表示用户取消（Ctrl+C / ESC）。
    """
    try:
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.formatted_text import HTML
    except ImportError:
        # 没有 prompt_toolkit，退回普通 input
        try:
            raw = input(hint or f"请选择 [1-{n}]：").strip()
            idx = int(raw) - 1
            return idx if 0 <= idx < n else None
        except (ValueError, EOFError, KeyboardInterrupt):
            return None

    kb = KeyBindings()

    for digit in range(1, min(n + 1, 10)):
        @kb.add(str(digit))
        def _(event, d=digit):
            event.app.current_buffer.text = str(d)
            event.app.current_buffer.validate_and_handle()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        # 写入空字符串后提交，调用方检测到空串视为取消
        event.app.current_buffer.text = ""
        event.app.current_buffer.validate_and_handle()

    label = hint or f"按 1-{n} 选择（直接按键，无需 Enter）："
    try:
        if prompt_session is not None:
            raw = prompt_session.prompt(
                HTML(f"<ansiyellow>{label}</ansiyellow> "),
                key_bindings=kb,
            ).strip()
        else:
            raw = input(label + " ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not raw:
        return None
    try:
        idx = int(raw) - 1
        return idx if 0 <= idx < n else None
    except ValueError:
        return None


def _ask_plan_questions(
    plan_text: str,
    prompt_session,
    console: Console,
) -> "Optional[str]":
    """解析计划文本中的问题，逐题展示并收集用户回答。

    Returns:
        格式化回答字符串（发回 PlanAgent），None 表示用户取消。
    """
    questions = _parse_plan_questions(plan_text)
    if not questions:
        return None

    answers: list[str] = []
    for i, (label, options) in enumerate(questions):
        # 确保最后一项是自定义
        if not any(kw in options[-1] for kw in ("自定义", "custom", "其他")):
            options = options + ["自定义：请输入你的具体想法"]

        n = len(options)
        while True:
            console.print(f"\n[bold cyan]{label}[/bold cyan]")
            for j, opt in enumerate(options, 1):
                if j == n:
                    console.print(f"  [dim]{j}. {opt}[/dim]")
                else:
                    console.print(f"  [bold]{j}.[/bold] {opt}")

            idx = _keypress_select(n, prompt_session)
            if idx is None:
                return None  # 用户取消

            if idx == n - 1:
                # 自定义输入框
                try:
                    from prompt_toolkit.formatted_text import HTML
                    if prompt_session is not None:
                        custom = prompt_session.prompt(
                            HTML("<ansiyellow>  请输入自定义内容：</ansiyellow> ")
                        ).strip()
                    else:
                        custom = input("  请输入自定义内容：").strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                if custom:
                    console.print(f"[green]  ✓ 自定义：{custom}[/green]")
                    answers.append(f"{label} → 自定义：{custom}")
                    break
                console.print("[yellow]  内容不能为空，请重新输入[/yellow]")
            else:
                console.print(f"[green]  ✓ 已选择：{options[idx]}[/green]")
                answers.append(f"{label} → {options[idx]}")
                break

    if not answers:
        return None
    return (
        "用户对各问题的回答如下：\n"
        + "\n".join(f"- {a}" for a in answers)
        + "\n\n请根据以上选择，输出最终执行计划。"
    )


# ─────────────────── 计划模式交互式选择器 ────────────────────

def _select_one_question(
    q_idx: int,
    q_total: int,
    header: str,
    options: list[str],
    default_idx: int,
    console: Console,
) -> "int | str":
    """用 prompt_toolkit Application 渲染单题选择器。

    返回选中的选项下标（int），或自定义文本（str）。
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import HTML

    CUSTOM_FLAG = any(kw in options[-1] for kw in ("自定义", "custom", "其他"))
    sel = [max(0, min(default_idx, len(options) - 1))]

    def get_text():
        rows: list[str] = []
        title = f"问题 {q_idx + 1}/{q_total}"
        if header:
            title += f"：{header}"
        rows.append(f"<ansicyan><b> {title} </b></ansicyan>")
        rows.append("")
        for i, opt in enumerate(options):
            label = opt[:72] + "…" if len(opt) > 72 else opt
            label = label.replace("<", "&lt;").replace(">", "&gt;")
            if i == sel[0]:
                rows.append(f"  <reverse><b> {i + 1}. {label} </b></reverse>")
            else:
                rows.append(f"     <dim>{i + 1}.</dim> {label}")
        rows.append("")
        rows.append(
            "<ansiblue> ↑/↓</ansiblue> 导航  "
            "<ansiblue>1-9</ansiblue> 快速选择  "
            "<ansiblue>Enter</ansiblue> 确认"
        )
        return HTML("\n".join(rows))

    kb = KeyBindings()

    @kb.add("up")
    def _(e):
        sel[0] = (sel[0] - 1) % len(options)

    @kb.add("down")
    def _(e):
        sel[0] = (sel[0] + 1) % len(options)

    @kb.add("enter")
    def _(e):
        e.app.exit(result=sel[0])

    @kb.add("escape")
    @kb.add("c-c")
    def _(e):
        e.app.exit(result=-1)

    for n in range(1, min(len(options) + 1, 10)):
        @kb.add(str(n))
        def _(e, _n=n - 1):
            sel[0] = _n
            e.app.exit(result=_n)

    app = Application(
        layout=Layout(Window(content=FormattedTextControl(get_text, focusable=True))),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )
    idx: int = app.run()
    if idx == -1:
        return default_idx  # ESC：保持默认

    if CUSTOM_FLAG and idx == len(options) - 1:
        console.print("[dim]请输入自定义内容：[/dim]")
        try:
            from prompt_toolkit import prompt as _pt_prompt
            custom = _pt_prompt("  > ").strip()
            return custom if custom else idx
        except Exception:
            return idx

    return idx


def _handle_plan_complete(
    agent,
    plan_agent,
    plan_text: str,
    plan_file,
    console: Console,
    prompt_session=None,
) -> "Optional[str]":
    """计划 Agent 完成后展示三选项，返回 _pending_input 或 None。

    选项：
      1. 清理上下文，按计划重新开始
      2. 直接开始执行（保持当前上下文）
      3. 继续补充计划内容
    """
    if plan_file:
        console.print(f"\n[dim]计划已保存: {plan_file}[/dim]")

    console.print(
        "\n[bold cyan]计划已完成，请选择下一步：[/bold cyan]\n"
        "  [bold]1.[/bold] 清理上下文，按计划重新开始（新对话）\n"
        "  [bold]2.[/bold] 直接开始执行（保持当前上下文）\n"
        "  [bold]3.[/bold] 继续补充计划内容\n"
    )

    choice = _keypress_select(3, prompt_session, "按 1-3 选择：")
    if choice is None:
        choice = 1  # 默认：直接执行

    if choice == 0:
        # 清理上下文 + 以计划内容作为新任务启动
        agent.state.messages = []
        agent.state.turn = 0
        agent.state.plan_mode = False
        plan_agent.reset()
        console.print("[bold green]上下文已清理，按计划重新开始...[/bold green]")
        return f"请按照以下计划执行：\n\n{plan_text}"

    elif choice == 1:
        # 保持上下文，直接切换到执行模式
        agent.state.plan_mode = False
        plan_agent.reset()
        console.print("[bold green]切换到执行模式，开始执行计划...[/bold green]")
        return f"请按照以下计划开始执行：\n\n{plan_text}"

    else:
        # 继续补充计划（plan_mode 保持 True，plan_agent 保留历史）
        console.print("[dim]继续补充计划内容，请输入补充说明...[/dim]")
        return None


# ─────────────────────────────────────────────────────────────

def run_interactive_loop(agent: CodingAgent, session_manager: SessionManager, cwd: str = ".") -> None:
    """主交互循环。"""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

        class _SafeFileHistory(FileHistory):
            """FileHistory 子类：写入前净化孤立代理字符，防止 Windows 下 UTF-8 编码崩溃。"""
            def store_string(self, string: str) -> None:
                safe = string.encode("utf-8", errors="replace").decode("utf-8")
                super().store_string(safe)
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
            if agent.state.plan_mode:
                n_tools = len(PLAN_MODE_READONLY_TOOLS)
                plan = f" · <ansigreen>[计划模式 · {n_tools} 只读工具]</ansigreen>"
            else:
                plan = ""
            return HTML(
                f" <b>{model}</b> · Turn {turn} · "
                f"{in_tok:,} tokens · ¥{cost:.4f}{plan}"
            )

        kb = KeyBindings()

        @kb.add("c-p")
        def toggle_plan_mode(event):
            agent.state.plan_mode = not agent.state.plan_mode
            if agent.state.plan_mode:
                plan_agent.reset()
            event.app.invalidate()

        @kb.add("c-o")
        def toggle_last_output(event):
            """Ctrl+O：切换最近一次工具输出的展开/折叠状态，并重新打印。"""
            from src.ui.collapsible_output import get_current

            co = get_current()
            if co is None:
                return

            co.toggle()

            # 将 Rich Panel 渲染到字符串缓冲，通过 prompt_toolkit 的
            # print_formatted_text + ANSI 打印到当前提示行上方
            try:
                import io
                from rich.console import Console as _RichConsole
                from prompt_toolkit.formatted_text import ANSI
                from prompt_toolkit import print_formatted_text as _pt_print

                buf = io.StringIO()
                tmp = _RichConsole(
                    file=buf,
                    highlight=False,
                    markup=True,
                    width=console.width,
                )
                tmp.print(co._build_panel())
                _pt_print(ANSI(buf.getvalue()))
            except Exception:
                pass

        prompt_session = PromptSession(
            history=_SafeFileHistory(str(history_file)),
            completer=completer,
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
            key_bindings=kb,
            bottom_toolbar=get_toolbar,
        )

        # 注入 ask_user 工具的 UI 实现（需要 prompt_session 和 console 的闭包）
        from src.tools.ask_user_tool import set_ask_user_handler
        set_ask_user_handler(
            lambda q, opts, ac: _ask_user_interactive(q, opts, ac, prompt_session, console)
        )

        _pending_input: Optional[str] = None
        plan_agent = PlanAgent(agent.llm, cwd, console)

        while True:
            try:
                if _pending_input is not None:
                    user_input = _pending_input
                    _pending_input = None
                else:
                    user_input = prompt_session.prompt(get_prompt).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]再见！[/dim]")
                break

            if not user_input:
                continue

            # 处理斜杠命令
            _plan_mode_before = agent.state.plan_mode
            _cmd_pending: list = []
            if user_input.startswith("/"):
                if handle_slash_command(user_input, agent, session_manager, cwd, _cmd_pending):
                    # /plan 命令开启计划模式时重置 plan_agent
                    if agent.state.plan_mode and not _plan_mode_before:
                        plan_agent.reset()
                    # /init 等命令可能推入待执行 prompt（在后台线程里跑，避免主线程死锁）
                    if _cmd_pending:
                        _pending_input = _cmd_pending[0]
                    continue

            console.print()

            # ── 计划模式：路由到 PlanAgent ──────────────────────────────────────
            if agent.state.plan_mode:

                def _run_plan_turn(context_msgs, task_input):
                    """在子线程中运行 PlanAgent 一轮，返回 (plan_text, plan_file) 或抛异常。"""
                    result_box: list = []
                    exc_box: list[Exception] = []

                    def _worker():
                        try:
                            result_box.append(plan_agent.run(context_msgs, task_input))
                        except Exception as ex:
                            exc_box.append(ex)

                    t = threading.Thread(target=_worker, daemon=True)
                    t.start()
                    try:
                        while t.is_alive():
                            t.join(timeout=0.05)
                    except KeyboardInterrupt:
                        plan_agent._cancel.set()
                        console.print("\n[yellow]已中断[/yellow]")
                        t.join(timeout=2.0)
                        return None, None, True  # cancelled

                    if exc_box:
                        raise exc_box[0]
                    if result_box:
                        pt, pf = result_box[0]
                        return pt, pf, False
                    return "", None, False

                # 首轮：传入主 Agent 历史 + 用户输入
                try:
                    plan_text, plan_file, cancelled = _run_plan_turn(
                        agent.state.messages, user_input
                    )
                except Exception as ex:
                    console.print(f"\n[bold red]计划 Agent 错误: {ex}[/bold red]")
                    continue

                if cancelled or plan_agent._cancel.is_set():
                    continue

                print()

                # LLM 主动调用 exit_plan_mode → 直接跳到计划完成处理
                if plan_agent.exit_requested:
                    _pending_input = _handle_plan_complete(
                        agent, plan_agent, plan_text, plan_file, console, prompt_session
                    )
                    continue

                # Q&A 轮次：若输出含待确认问题，逐题交互后再投回 PlanAgent
                MAX_QA = 6
                for _ in range(MAX_QA):
                    if not _has_plan_questions(plan_text):
                        break  # 无问题 → 已是最终计划

                    answers_text = _ask_plan_questions(plan_text, prompt_session, console)
                    if answers_text is None:
                        # 用户取消 → 退出计划模式
                        cancelled = True
                        break

                    print()
                    try:
                        plan_text, plan_file, cancelled = _run_plan_turn([], answers_text)
                    except Exception as ex:
                        console.print(f"\n[bold red]计划 Agent 错误: {ex}[/bold red]")
                        cancelled = True
                        break

                    if cancelled or plan_agent._cancel.is_set():
                        break
                    print()

                if cancelled or plan_agent._cancel.is_set():
                    continue

                # 最终计划完成 → 展示后续选项
                _pending_input = _handle_plan_complete(
                    agent, plan_agent, plan_text, plan_file, console, prompt_session
                )
                continue

            # ── 普通模式：路由到主 CodingAgent ─────────────────────────────────
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
                if t.is_alive():
                    t.join(timeout=8.0)
                if t.is_alive():
                    console.print("[yellow]警告：工作线程仍未退出，后台可能仍有网络请求[/yellow]")
            else:
                if _exc:
                    console.print(f"\n[bold red]错误: {_exc[0]}[/bold red]")
            print()

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
        super_mode=args.super_mode,
    )

    # 初始化代码索引管理器
    index_manager = get_index_manager(project_root)
    set_index_manager(index_manager)

    # 初始化 LSP 客户端（懒启动，首次调用工具时才真正连接）
    set_lsp_root(project_root)

    # 加载 Skills（内置 + 用户自定义 ~/.myvibe/skills/）
    from src.skills.skill_registry import get_registry as _get_skill_registry
    _skill_count = _get_skill_registry().load_all()
    if _skill_count:
        console.print(f"[dim]已加载 {_skill_count} 个 Skills[/dim]")

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
        if args.super_mode:
            console.print(Panel(
                "[bold red]⚡ Super 模式已开启[/bold red]  [dim]所有操作无需确认[/dim]",
                border_style="red", expand=False,
            ))
        sync_and_display_memory(console, index_manager)
        exit_code = run_headless(agent, args.print)
        sys.exit(exit_code)
    else:
        display_welcome(console, session_id, model_name, project_root, tools_count=tools_count)
        if args.super_mode:
            console.print(Panel(
                "[bold red]⚡ Super 模式已开启[/bold red]  [dim]所有操作无需确认，使用 /super 可随时关闭[/dim]",
                border_style="red", expand=False,
            ))
        sync_and_display_memory(console, index_manager)
        run_interactive_loop(agent, session_manager, cwd)


if __name__ == "__main__":
    main()
