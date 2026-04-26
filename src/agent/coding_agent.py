"""CodingAgent：核心 agentic 循环、权限管理、工具并行执行。"""
from __future__ import annotations

import threading
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .base_agent import BaseAgent

from .state import AgentState, SessionManager
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient, ToolCall
from ..llm.prompts import build_system_prompt, build_tool_descriptions
from ..logger.structured_logger import StructuredLogger
from ..tools.base_tool import ToolRegistry, ToolResult
from ..tools.context_tools import set_context_manager
from ..tools.index.tools import set_index_manager
# 自动版本管理已下线：保留 git.py 模块定义，但不再每轮自动 commit。
# 需要重启用时取消注释下一行 + run_turn 末尾对应调用。
# from ..tools.git import auto_commit_turn
from ..tools.index.manager import get_index_manager

# 自动允许（无需确认）的工具列表（可由配置覆盖）
DEFAULT_AUTO_ALLOW = {
    "read_file", "search_in_file", "glob_files", "grep_files",
    "git_status", "git_diff",
    "find_symbol", "lsp_hover",
    "ask_user",   # 询问用户 = 本身就是用户交互，无需二次确认
}

MODEL_MAX_TOKENS = 200_000



class PermissionManager:
    """工具权限管理器：allow/confirm/deny 三档控制。"""

    def __init__(
        self,
        auto_allow: Optional[set[str]] = None,
        require_confirm: Optional[set[str]] = None,
        deny: Optional[set[str]] = None,
        console: Optional[Console] = None,
        super_mode: bool = False,
    ) -> None:
        self.auto_allow = auto_allow or DEFAULT_AUTO_ALLOW
        self.require_confirm = require_confirm or {"write_file", "edit_file", "shell", "git_commit"}
        self.deny = deny or set()
        self.console = console or Console()
        self.super_mode = super_mode
        # 本会话中用户选择 "always" 允许的工具
        self._always_allow: set[str] = set()
        # 由 CodingAgent 注入，用于 Ctrl+C 时跳过 stdin 阻塞
        self._cancel_event: Optional[threading.Event] = None
        # 串行化权限弹窗：并行工具调用时只允许一个提示同时占用 stdin
        self._prompt_lock = threading.Lock()

    def toggle_super(self) -> bool:
        """切换 super 模式，返回切换后的状态。"""
        self.super_mode = not self.super_mode
        return self.super_mode

    def check(self, tool_name: str, args: dict) -> bool:
        """主权限检查，返回 True 表示允许执行。"""
        if self.super_mode:
            return True
        if tool_name in self.deny:
            return False
        if tool_name in self.auto_allow or tool_name in self._always_allow:
            return True
        if tool_name in self.require_confirm:
            return self.ask_user(tool_name, args)
        # 未知工具：默认确认
        return self.ask_user(tool_name, args)

    def ask_user(self, tool_name: str, args: dict, description: str = "") -> bool:
        """显示 Rich 确认弹窗，等待 y/n/always。
        若 _cancel_event 已触发，立即拒绝，不阻塞 stdin。
        使用 _prompt_lock 串行化，确保并行工具调用时不会多个弹窗同时抢 stdin。
        """
        # 已取消：快速拒绝，完全不触碰 stdin
        if self._cancel_event and self._cancel_event.is_set():
            return False

        with self._prompt_lock:
            # 拿到锁后再次检查取消状态（等锁期间可能已经 Ctrl+C）
            if self._cancel_event and self._cancel_event.is_set():
                return False

            args_preview = "\n".join(
                f"  {k}: {str(v)[:100]}" for k, v in list(args.items())[:5]
            )
            panel_content = args_preview or "(无参数)"
            if description:
                panel_content = f"{description}\n\n{panel_content}"

            self.console.print(Panel(
                panel_content,
                title=f"[bold yellow]权限确认: {tool_name}[/bold yellow]",
                border_style="yellow",
                expand=False,
            ))

            answer = self._cancellable_prompt()
            if answer is None:
                # 取消事件触发，已打印提示
                return False
            if answer == "a":
                self._always_allow.add(tool_name)
                return True
            return answer == "y"

    def _cancellable_prompt(self) -> Optional[str]:
        """以 0.2s 为间隔轮询 stdin，每轮检查 _cancel_event。

        返回用户输入的字符（"y"/"n"/"a"），取消时返回 None。
        在不支持 select 的环境（Windows/非 TTY）自动降级为 Prompt.ask。
        """
        import sys
        import os

        # Windows：select.select 仅支持 socket，不支持 stdin fd，直接降级
        if sys.platform == "win32":
            return self._blocking_prompt()

        # 非 TTY：降级为普通阻塞 Prompt
        try:
            import select
            if not hasattr(select, "select") or not os.isatty(sys.stdin.fileno()):
                raise OSError("not a tty")
        except OSError:
            return self._blocking_prompt()

        prompt_text = (
            "[bold yellow]允许执行？[/bold yellow]"
            " [dim](y=是 / n=否 / a=本会话始终允许)[/dim] "
        )
        self.console.print(prompt_text, end="")

        try:
            while True:
                if self._cancel_event and self._cancel_event.is_set():
                    self.console.print("\n[yellow](已中断，自动拒绝)[/yellow]")
                    return None
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if ready:
                    line = sys.stdin.readline().strip().lower()
                    if line in ("y", "n", "a"):
                        return line
                    # 非法输入：默认 y
                    return "y"
        except (EOFError, KeyboardInterrupt):
            return None

    def _blocking_prompt(self) -> Optional[str]:
        """普通阻塞式 Prompt.ask（降级路径）。"""
        try:
            answer = Prompt.ask(
                "[bold yellow]允许执行？[/bold yellow] [dim](y=是 / n=否 / a=本会话始终允许)[/dim]",
                choices=["y", "n", "a"],
                default="y",
            ).lower()
            return answer
        except (EOFError, KeyboardInterrupt):
            return None

    def add_allow_rule(self, rule: str) -> None:
        """添加允许规则（工具名加入 auto_allow）。"""
        self.auto_allow.add(rule)


class CodingAgent(BaseAgent):
    """编程 AI 代理：核心 agentic 循环实现。"""

    def __init__(
        self,
        llm_client: LLMClient,
        state: AgentState,
        session_manager: SessionManager,
        permission_manager: PermissionManager,
        context_manager: ContextManager,
        logger: StructuredLogger,
        console: Console,
        config: dict,
    ) -> None:
        self.llm = llm_client
        self.state = state
        self.session_manager = session_manager
        self.permission = permission_manager
        self.context_manager = context_manager
        self.logger = logger
        self.console = console
        self.config = config
        self.compress_threshold = config.get("agent", {}).get("context_compress_threshold", 0.92)
        self.compress_keep_recent = config.get("agent", {}).get("compress_keep_recent", 10)

        # 注入全局 ContextManager
        set_context_manager(context_manager)

        # 代码索引管理器：单例 + 注入到 index tools
        self._index_manager = get_index_manager(str(context_manager.project_root))
        set_index_manager(self._index_manager)

        # 完整工具 schema（始终保留完整版，plan_mode 时动态过滤）
        # 追加 spawn_agent（不通过 ToolRegistry，由 handle_tool_calls 特殊处理）
        from ..tools.agent_tools import SPAWN_AGENT_SCHEMA
        self._tools_schema = ToolRegistry.all_tools_schema() + [SPAWN_AGENT_SCHEMA]
        # 中断信号：由外部（main loop）在 Ctrl+C 时 set()
        self._cancel = threading.Event()
        # 命名线程引用：退出前可 join 等待完成
        self._naming_thread: Optional[threading.Thread] = None

        # 将 cancel 事件注入权限管理器，Ctrl+C 时跳过 stdin 阻塞
        self.permission._cancel_event = self._cancel

        # 系统提示词只在初始化时构建一次，后续不再修改
        self._system = build_system_prompt(
            tool_descriptions="",
            cwd=self.state.cwd,
            tool_count=len(self._tools_schema),
        )
        self.state.system_prompt = self._system

    def run_turn(self, user_input: str) -> str:
        """核心 agentic 循环。

        流程：
        1. 追加用户消息
        2. 检查是否需要压缩
        3. 循环调用 LLM → 处理工具调用 → 直到 end_turn
        """
        self.state.append_user(user_input)
        self.logger.set_turn(self.state.turn)
        self.logger.turn_start(self.state.turn, user_input)

        self.check_compress()

        # 模型路由：若配置启用则按任务类型动态切换模型
        _original_model: str | None = None
        if self.config.get("agent", {}).get("model_routing", False):
            try:
                from ..llm.model_router import route_model
                cfg = route_model(self.state.messages)
                if self.llm.model != cfg.model_id:
                    _original_model = self.llm.model
                    self.llm.model = cfg.model_id
                    self.console.print(
                        f"[dim]→ 路由模型: {cfg.model_id}（{cfg.description}）[/dim]"
                    )
            except Exception:
                pass

        system = self._system
        active_tools = self._tools_schema

        final_text = ""
        iteration = 0
        turn_total_input = 0
        turn_total_output = 0
        turn_total_cached = 0
        turn_total_reasoning = 0
        turn_total_cost = 0.0
        # 单 turn 内工具调用默认不设硬上限 —— runaway 应靠 context 压缩与用户的
        # 取消信号兜底。用户可通过 config 里 agent.max_iterations 显式限制。
        _max_iter_cfg = self.config.get("agent", {}).get("max_iterations")
        MAX_ITERATIONS = int(_max_iter_cfg) if _max_iter_cfg is not None else None
        while MAX_ITERATIONS is None or iteration < MAX_ITERATIONS:
            if self._cancel.is_set():
                break
            iteration += 1

            # 估算 token
            estimated_tokens = self.llm.count_tokens(self.state.messages, system)
            self.logger.llm_request(
                model=self.llm.model,
                messages_count=len(self.state.messages),
                estimated_tokens=estimated_tokens,
                tools_count=len(active_tools),
            )

            # 流式调用 LLM
            response = self.llm.stream_chat(
                messages=self.state.messages,
                system=system,
                tools=active_tools,
                on_text=self._on_stream_text,
                on_tool_start=self.logger.llm_stream_tool,
                cancel_event=self._cancel,
            )

            # 流式文本结束后换行，避免 log 紧跟在响应末尾
            if response.text_content:
                print()

            # 中断时跳过所有后续日志和状态更新
            if self._cancel.is_set():
                break

            _in       = response.usage["input_tokens"]
            _out      = response.usage["output_tokens"]
            _cached   = response.usage.get("cached_tokens", 0)
            _thinking = response.usage.get("reasoning_tokens", 0)
            turn_total_input     += _in
            turn_total_output    += _out
            turn_total_cached    += _cached
            turn_total_reasoning += _thinking
            turn_total_cost      += response.cost_usd
            self.logger.llm_response(
                stop_reason=response.stop_reason,
                input_tokens=_in,
                output_tokens=_out,
                cost_usd=response.cost_usd,
                elapsed_ms=response.elapsed_ms,
                cached_tokens=_cached,
                reasoning_tokens=_thinking,
            )

            self.state.update_usage(
                response.usage["input_tokens"],
                response.usage["output_tokens"],
                response.cost_usd,
                reasoning_tokens=response.usage.get("reasoning_tokens", 0),
            )

            # 构建 assistant content blocks
            content_blocks: list[dict] = []
            if response.text_content:
                content_blocks.append({"type": "text", "text": response.text_content})
                final_text = response.text_content

            for tc in response.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.tool_use_id,
                    "name": tc.name,
                    "input": tc.input,
                })

            if content_blocks:
                self.state.append_assistant(content_blocks)

            # 保存会话
            self.session_manager.save(self.state)

            # 检查 stop_reason：有 tool_calls 就执行，否则结束
            if not response.tool_calls or self._cancel.is_set():
                break

            # 执行工具调用
            tool_results = self.handle_tool_calls(response.tool_calls)
            self.state.append_tool_results(tool_results)

        # Turn 1 结束后，用隔离子 Agent 命名会话（不影响主上下文）
        if self.state.turn == 1 and not self.state.name and not self._cancel.is_set():
            self._name_session_async()

        if not self._cancel.is_set():
            self.logger.turn_end(
                self.state.turn, iteration,
                turn_total_input, turn_total_output, turn_total_cached,
                turn_total_reasoning, turn_total_cost,
            )
            # 自动版本管理已下线 —— 不再每轮自动 commit。
            # 需要重启用时恢复以下代码块（并取消顶部 import 注释）：
            # commit_hash = auto_commit_turn(
            #     self.state.cwd, self.state.turn, user_input, self.state.session_id
            # )
            # if commit_hash:
            #     self.logger.log("git_auto_commit",
            #                     {"turn": self.state.turn, "hash": commit_hash})

        # 恢复原始模型（路由仅作用于本轮）
        if _original_model is not None:
            self.llm.model = _original_model

        return final_text

    def handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[dict]:
        """串行执行工具调用（配合 parallel_tool_calls=False，每次只有一个工具）。"""
        results = [None] * len(tool_calls)

        def execute_one(idx: int, tc: ToolCall) -> tuple[int, dict]:
            tool_name = tc.name
            tool_use_id = tc.tool_use_id
            args = tc.input
            start_ms = int(time.monotonic() * 1000)

            if self._cancel.is_set():
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "已中断",
                    "is_error": True,
                }

            self.logger.tool_call(tool_name, args, tool_use_id)
            self._render_tool_call(tool_name, args)

            # spawn_agent：特殊处理，创建隔离子代理
            if tool_name == "spawn_agent":
                from .sub_agent import SubAgent
                task = args.get("task", "")
                context = args.get("context", "")
                self.console.print(f"[dim]→ 启动子代理：{task[:60]}...[/dim]"
                                   if len(task) > 60 else
                                   f"[dim]→ 启动子代理：{task}[/dim]")
                try:
                    sub = SubAgent(self.llm, self._tools_schema)
                    result_text = sub.run(task=task, context=context)
                except Exception as e:
                    result_text = f"子代理执行失败: {e}"
                elapsed = int(time.monotonic() * 1000) - start_ms
                self.logger.tool_result(tool_name, tool_use_id, True, result_text[:80], elapsed, result_text)
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_text,
                    "is_error": False,
                }

            # read_file 已读检测：相同 (file_path, offset, limit) 且文件 mtime ≤ 上次
            # 读取时刻 → 直接返回提示，避免把同一段内容塞回上下文浪费 token。
            # offset/limit 任一不同则视作不同片段，正常读取。
            if tool_name == "read_file":
                _hit = self._check_read_file_already_read(args)
                if _hit is not None:
                    elapsed = int(time.monotonic() * 1000) - start_ms
                    self.logger.tool_result(
                        tool_name, tool_use_id, True, _hit[:80], elapsed, _hit
                    )
                    return idx, {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": _hit,
                        "is_error": False,
                    }

            # 权限检查
            description = args.get("description", "")
            if not self.permission.check(tool_name, args):
                self.logger.permission_check(tool_name, "execute", False)
                result_content = f"工具 '{tool_name}' 执行被用户拒绝"
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_content,
                    "is_error": True,
                }

            self.logger.permission_check(tool_name, "execute", True)

            # ── shell 工具：流式输出 + 实时可折叠面板 ────────────────
            if tool_name == "shell":
                from ..ui.collapsible_output import CollapsibleOutput
                from ..ui.key_listener import CtrlOListener

                co = CollapsibleOutput(
                    self.console,
                    title=f"[bold cyan]⚡ shell[/bold cyan]",
                    border_style="cyan",
                    interactive=True,
                )
                try:
                    shell_tool = ToolRegistry.instantiate("shell")
                    with co:
                        with CtrlOListener(co.toggle):
                            result = shell_tool.execute_stream(
                                command=args.get("command", ""),
                                on_line=co.feed,
                                timeout=args.get("timeout", 120000),
                                working_dir=args.get("working_dir"),
                            )
                        co.finish()
                except Exception as e:
                    from ..tools.base_tool import ToolResult as _TR
                    result = _TR(content=f"shell 执行异常: {e}", is_error=True)

                # 注册为"当前输出"，供输入提示阶段的 Ctrl+O 访问
                from ..ui.collapsible_output import set_current
                set_current(co)

                # 用成功/失败颜色更新显示已通过 Live 完成，仅记录日志
                elapsed = int(time.monotonic() * 1000) - start_ms
                full_content = result.content or ""
                summary = full_content[:80].replace("\n", " ")
                self.logger.tool_result(
                    tool_name, tool_use_id, not result.is_error,
                    summary, elapsed, full_content,
                    pre_rendered=True,
                )
                return idx, result.to_api_dict(tool_use_id)

            try:
                tool = ToolRegistry.instantiate(tool_name)
                result = tool.execute(**args)

                # edit_file 后自动失效缓存
                if tool_name == "edit_file" and not result.is_error:
                    file_path = args.get("file_path", "")
                    if file_path:
                        self.context_manager.invalidate(file_path)

                # read_file 成功 → 记录 (file_path, offset, limit, read_time) + 统计
                if tool_name == "read_file" and not result.is_error:
                    self._record_read_file(args)
                    self.state.memory_tool_calls += 1
                    self.state.memory_tool_tokens += len(result.content or "") // 4

                # write_file / edit_file 成功后自动附加语法验证
                if tool_name in ("write_file", "edit_file") and not result.is_error:
                    written_path = args.get("file_path", "")
                    if written_path:
                        try:
                            from ..tools.compile_tool import validate_file_str
                            validation = validate_file_str(written_path)
                            result = ToolResult(
                                content=f"{result.content or ''}\n\n{validation}",
                                is_error=result.is_error,
                                metadata=result.metadata,
                            )
                        except Exception:
                            pass  # 验证失败不阻断写入

                elapsed = int(time.monotonic() * 1000) - start_ms
                full_content = result.content or ""
                summary = full_content[:80].replace("\n", " ")
                self.logger.tool_result(tool_name, tool_use_id, not result.is_error, summary, elapsed, full_content)
                return idx, result.to_api_dict(tool_use_id)

            except KeyError:
                elapsed = int(time.monotonic() * 1000) - start_ms
                error_msg = f"工具 '{tool_name}' 未注册"
                self.logger.tool_error(tool_name, tool_use_id, KeyError(error_msg))
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": error_msg,
                    "is_error": True,
                }
            except Exception as e:
                elapsed = int(time.monotonic() * 1000) - start_ms
                self.logger.tool_error(tool_name, tool_use_id, e)
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"工具执行异常: {e}",
                    "is_error": True,
                }

        for i, tc in enumerate(tool_calls):
            idx, result = execute_one(i, tc)
            results[idx] = result

        return results

    def check_compress(self) -> bool:
        """检查上下文占比，超过阈值时压缩。"""
        ratio = self.state.get_context_ratio(MODEL_MAX_TOKENS)
        if ratio > 0.8:
            self.logger.context_ratio(ratio, self.state.last_response_input_tokens, MODEL_MAX_TOKENS)

        if ratio >= self.compress_threshold:
            self.console.print("[bold blue]正在压缩对话历史...[/bold blue]")
            self.state = self.session_manager.compress_history(
                self.state, self.llm, self.compress_keep_recent
            )
            self.session_manager.save(self.state)
            return True
        return False

    def _on_stream_text(self, delta: str) -> None:
        """流式文本 delta 实时打印（直接 print，无 Live 重绘）。"""
        print(delta, end="", flush=True)
        self.logger.llm_stream_token(delta, 0)

    def _render_tool_call(self, tool_name: str, args: dict) -> None:
        """（已在 logger.tool_call 中渲染，此处保留扩展点）"""
        pass

    @staticmethod
    def _normalize_read_file_key(args: dict) -> Optional[tuple[str, int, int]]:
        """从 args 解出 (resolved_path, offset, limit)，失败返回 None。"""
        fp = args.get("file_path", "")
        if not fp:
            return None
        try:
            from ..utils.path import safe_resolve
            resolved = safe_resolve(fp)
        except Exception:
            return None
        try:
            off = int(args.get("offset", 1) or 1)
            lim = int(args.get("limit", 2000) or 2000)
        except (ValueError, TypeError):
            return None
        return str(resolved), off, lim

    def _check_read_file_already_read(self, args: dict) -> Optional[str]:
        """命中已读返回提示串；未命中返回 None。

        命中条件：log 中存在精确匹配 (resolved_path, offset, limit) 的记录，
        且当前文件 mtime ≤ 上次 read_time（即未被编辑过）。
        """
        key = self._normalize_read_file_key(args)
        if key is None:
            return None
        resolved_str, off, lim = key
        try:
            from pathlib import Path
            p = Path(resolved_str)
            if not p.exists() or not p.is_file():
                return None
            mtime = p.stat().st_mtime
        except Exception:
            return None

        # 取最近一次同 key 的记录
        for record in reversed(self.state.read_file_log):
            if (
                record.get("file_path") == resolved_str
                and record.get("offset") == off
                and record.get("limit") == lim
            ):
                last_read = record.get("read_time", 0.0)
                if mtime <= last_read:
                    fmt = lambda ts: time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(ts)
                    )
                    return (
                        f"[已读提示] 你已读过该文件的相同片段，且文件自上次读取后未被编辑：\n"
                        f"  file_path = {args.get('file_path', '')}\n"
                        f"  offset    = {off}\n"
                        f"  limit     = {lim}\n"
                        f"  上次读取 = {fmt(last_read)}\n"
                        f"  文件 mtime = {fmt(mtime)}\n"
                        f"内容已在之前的对话历史中，无需重复读取。"
                        f"如需查看不同行段，请改变 offset/limit。"
                    )
                # 文件已编辑过 → 允许读
                return None
        return None

    def _record_read_file(self, args: dict) -> None:
        """记录一次成功的 read_file 调用，去重保留最新的同 key 记录。"""
        key = self._normalize_read_file_key(args)
        if key is None:
            return
        resolved_str, off, lim = key
        # 去掉旧的同 key 记录，再追加新的（保持单条最新）
        self.state.read_file_log = [
            r for r in self.state.read_file_log
            if not (
                r.get("file_path") == resolved_str
                and r.get("offset") == off
                and r.get("limit") == lim
            )
        ]
        self.state.read_file_log.append({
            "file_path": resolved_str,
            "offset": off,
            "limit": lim,
            "read_time": time.time(),
        })
        # 限长，避免会话长跑后无限增长
        if len(self.state.read_file_log) > 200:
            self.state.read_file_log = self.state.read_file_log[-200:]

    def _name_session_async(self) -> None:
        """子 Agent：根据第一条用户消息为会话命名。

        完全隔离：直接调用 _stream_chat_impl 绕过历史记录，
        结果只写入 state.name，不污染主对话上下文。
        """
        first_msg = next(
            (m for m in self.state.messages if m.get("role") == "user"), None
        )
        first_content = first_msg.get("content", "") if first_msg else ""
        if not first_content or not isinstance(first_content, str):
            return

        state_ref = self.state
        session_manager_ref = self.session_manager
        llm_ref = self.llm

        def _run() -> None:
            try:
                resp = llm_ref.chat_isolated(
                    messages=[{"role": "user", "content":
                        f"用不超过10个字为这个对话命名（只输出名称，无标点无解释）：{first_content[:300]}"}],
                    system="你是对话命名助手，只输出简洁标题，不超过10个字，无标点。",
                )
                name = (resp.text_content or "").strip()[:20]
                if name:
                    state_ref.name = name
                # 将命名子 Agent 的 token 计入会话总用量（不覆盖上下文比例基准）
                state_ref.update_usage(
                    input_tokens=resp.usage.get("input_tokens", 0),
                    output_tokens=resp.usage.get("output_tokens", 0),
                    cost_usd=resp.cost_usd,
                    reasoning_tokens=resp.usage.get("reasoning_tokens", 0),
                    update_last_response=False,
                )
                session_manager_ref.save(state_ref)
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True, name="session-namer")
        self._naming_thread = t
        t.start()

