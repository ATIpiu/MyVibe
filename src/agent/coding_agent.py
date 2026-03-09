"""CodingAgent：核心 agentic 循环、权限管理、工具并行执行。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .base_agent import BaseAgent
from .project_init import load_myvibe
from .state import AgentState, SessionManager
from ..context.context_manager import ContextManager
from ..llm.client import LLMClient, ToolCall
from ..llm.prompts import build_system_prompt, build_tool_descriptions, load_memory_context
from ..logger.structured_logger import StructuredLogger
from ..tools.base_tool import ToolRegistry
from ..tools.context_tools import set_context_manager
from ..tools.memory_tools import set_memory_manager
from ..tools.git import auto_commit_turn
from ..memory.memory_manager import get_memory_manager

# 自动允许（无需确认）的工具列表（可由配置覆盖）
DEFAULT_AUTO_ALLOW = {
    "read_file", "search_in_file", "git_status", "git_diff",
    "read_memory", "lsp_hover",
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
    ) -> None:
        self.auto_allow = auto_allow or DEFAULT_AUTO_ALLOW
        self.require_confirm = require_confirm or {"write_file", "edit_file", "shell", "git_commit"}
        self.deny = deny or set()
        self.console = console or Console()
        # 本会话中用户选择 "always" 允许的工具
        self._always_allow: set[str] = set()

    def check(self, tool_name: str, args: dict) -> bool:
        """主权限检查，返回 True 表示允许执行。"""
        if tool_name in self.deny:
            return False
        if tool_name in self.auto_allow or tool_name in self._always_allow:
            return True
        if tool_name in self.require_confirm:
            return self.ask_user(tool_name, args)
        # 未知工具：默认确认
        return self.ask_user(tool_name, args)

    def ask_user(self, tool_name: str, args: dict, description: str = "") -> bool:
        """显示 Rich 确认弹窗，等待 y/n/always。"""
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

        try:
            answer = Prompt.ask(
                "[bold yellow]允许执行？[/bold yellow] [dim](y=是 / n=否 / a=本会话始终允许)[/dim]",
                choices=["y", "n", "a"],
                default="y",
            ).lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "a":
            self._always_allow.add(tool_name)
            return True
        return answer == "y"

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
        self.max_tool_workers = config.get("agent", {}).get("max_tool_workers", 4)
        self.compress_threshold = config.get("agent", {}).get("context_compress_threshold", 0.92)
        self.compress_keep_recent = config.get("agent", {}).get("compress_keep_recent", 10)

        # 注入全局 ContextManager
        set_context_manager(context_manager)

        # 记忆管理器：单例 + 注入到 memory_tools
        self._memory_manager = get_memory_manager(str(context_manager.project_root))
        set_memory_manager(self._memory_manager)

        # 构建工具 schema
        self._tools_schema = ToolRegistry.all_tools_schema()

        # 中断信号：由外部（main loop）在 Ctrl+C 时 set()
        self._cancel = threading.Event()

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

        # 构建系统提示词（不含工具描述，tools schema 已通过 API 参数传递）
        memory = load_memory_context(
            "~/.claude/AGENT.md",
            str(self.context_manager.project_root / "AGENT.md"),
        )
        myvibe = load_myvibe(self.state.cwd)
        proactive_memory = self._build_proactive_memory(user_input)
        system = build_system_prompt(
            tool_descriptions="",
            cwd=self.state.cwd,
            memory_context=memory,
            tool_count=len(self._tools_schema),
            proactive_memory=proactive_memory,
            myvibe_context=myvibe,
            plan_mode=self.state.plan_mode,
        )

        # 将系统提示词同步到 state，便于 .agent_sessions JSONL 完整记录
        self.state.system_prompt = system

        final_text = ""
        iteration = 0
        turn_total_input = 0
        turn_total_output = 0
        turn_total_cached = 0
        turn_total_reasoning = 0
        turn_total_cost = 0.0
        MAX_ITERATIONS = 20  # 防止无限循环（降低上限减少 token 累积）
        while iteration < MAX_ITERATIONS:
            if self._cancel.is_set():
                break
            iteration += 1

            # 估算 token
            estimated_tokens = self.llm.count_tokens(self.state.messages, system)
            self.logger.llm_request(
                model=self.llm.model,
                messages_count=len(self.state.messages),
                estimated_tokens=estimated_tokens,
                tools_count=len(self._tools_schema),
            )

            # 流式调用 LLM
            response = self.llm.stream_chat(
                messages=self.state.messages,
                system=system,
                tools=self._tools_schema,
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

        if not self._cancel.is_set():
            self.logger.turn_end(
                self.state.turn, iteration,
                turn_total_input, turn_total_output, turn_total_cached,
                turn_total_reasoning, turn_total_cost,
            )
            commit_hash = auto_commit_turn(
                self.state.cwd, self.state.turn, user_input, self.state.session_id
            )
            if commit_hash:
                self.logger.log("git_auto_commit", {"turn": self.state.turn, "hash": commit_hash})

        return final_text

    def handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[dict]:
        """并行执行工具调用（ThreadPoolExecutor，max_workers=4）。"""
        results = [None] * len(tool_calls)

        def execute_one(idx: int, tc: ToolCall) -> tuple[int, dict]:
            tool_name = tc.name
            tool_use_id = tc.tool_use_id
            args = tc.input

            if self._cancel.is_set():
                return idx, {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "已中断",
                    "is_error": True,
                }

            self.logger.tool_call(tool_name, args, tool_use_id)
            self._render_tool_call(tool_name, args)

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
            start_ms = int(time.monotonic() * 1000)

            # 已读文件拦截：避免重复 read_file，节省 token
            if tool_name == "read_file":
                file_path = args.get("file_path", "")
                if file_path and self.state.is_file_read(file_path):
                    msg = (
                        f"文件 {file_path} 在本会话已完整读取过，无需重复读取。"
                        "请直接基于上下文已有内容操作。"
                        "如需查看特定函数详情，请用 read_memory(scope='function', function_key=...)"
                    )
                    elapsed = int(time.monotonic() * 1000) - start_ms
                    self.logger.tool_result(tool_name, tool_use_id, True, msg[:80], elapsed, msg)
                    return idx, {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": msg,
                        "is_error": False,
                    }

            try:
                tool = ToolRegistry.instantiate(tool_name)
                result = tool.execute(**args)

                # edit_file 后自动失效缓存
                if tool_name == "edit_file" and not result.is_error:
                    file_path = args.get("file_path", "")
                    if file_path:
                        self.context_manager.invalidate(file_path)

                # 只有 read_file 成功才标记文件已读（编辑≠读过）
                if tool_name == "read_file" and not result.is_error:
                    self.state.mark_file_read(args.get("file_path", ""))

                # 记忆工具调用：累计注入上下文的 token 量
                if tool_name == "read_memory" and not result.is_error:
                    self.state.memory_tool_calls += 1
                    self.state.memory_tool_tokens += len(result.content or "") // 4

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

        with ThreadPoolExecutor(max_workers=self.max_tool_workers) as executor:
            futures = {executor.submit(execute_one, i, tc): i for i, tc in enumerate(tool_calls)}
            for future in as_completed(futures):
                idx, result = future.result()
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

    def _build_proactive_memory(self, user_input: str) -> str:
        """根据用户输入主动搜索相关函数记忆，注入系统提示词。"""
        try:
            results = self._memory_manager.search(query=user_input, top_k=8)
            if not results:
                return ""
            lines = ["## 相关函数记忆（自动检索）", ""]
            for module_path, qualname, func_data in results:
                key = f"{module_path}:{qualname}"
                lines.append(f"- `{key}`: {func_data.purpose or '（无描述）'}")
            return "\n".join(lines)
        except Exception:
            return ""
