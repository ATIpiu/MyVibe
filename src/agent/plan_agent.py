"""计划 Agent：独立系统提示词，只读工具，生成并保存执行计划到 .myvibe/plans/。"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from ..llm.client import LLMClient, ToolCall
from ..tools.base_tool import ToolRegistry

# 计划模式只读工具集合（主 Agent 导入此处以保持一致）
PLAN_MODE_READONLY_TOOLS = {
    "read_file", "search_in_file",
    "glob_files", "grep_files",
    "git_status", "git_diff",
    "read_memory", "rebuild_memory",
    "lsp_hover", "lsp_definition",
    "get_context_info",
    "ask_user",
}

PLAN_SYSTEM_PROMPT = """你是一个专业的代码任务规划助手，负责分析用户任务并制定详细执行计划。

## 核心约束
- 只能使用只读工具（read_file、grep_files、glob_files、git_status、read_memory 等）
- 禁止调用任何写入或执行类工具（write_file、edit_file、shell、git_commit 等）
- 目标是输出完整可执行的计划供用户确认，不执行任何修改

## 严禁行为（极其重要）
- **严禁**在回复文本中写出任何代码实现，包括：函数体、算法代码、完整代码片段、伪代码
- 计划只描述「在哪个文件、加什么函数/类、改哪一行、做什么事」，**绝对不展示具体代码**
- 即使任务看起来很简单（如"实现冒泡排序"），也只写步骤描述，不写代码

## 遇到不确定项时（重要）
若任务存在歧义或需要用户澄清，**调用 ask_user 工具**逐个提问，不要把问题写在文本里。
- 每次只问一个问题
- 提供 2-4 个选项（最后一项建议加"其他/自定义"）
- 收集完所有必要信息后，再输出完整计划

## 最终计划输出格式
所有问题确认后，输出：

---
## 执行计划

**目标**：[任务目标]

**涉及文件**：
- `path/to/file.py`：[涉及原因]

**执行步骤**：
1. [第一步：在哪个文件、做什么操作，不写代码]
2. [第二步]
...

**预期结果**：[完成后的状态]

---
"""


class PlanAgent:
    """计划模式独立 Agent：使用独立系统提示词和只读工具进行规划。"""

    def __init__(
        self,
        llm_client: LLMClient,
        cwd: str,
        console: Console,
    ) -> None:
        self.llm = llm_client
        self.cwd = cwd
        self.console = console
        self._cancel = threading.Event()
        all_tools = ToolRegistry.all_tools_schema()
        self._tools = [t for t in all_tools if t["name"] in PLAN_MODE_READONLY_TOOLS]
        self._plan_dir = Path(cwd) / ".myvibe" / "plans"
        # 计划对话历史（跨多次 run() 调用保持，支持"继续补充"）
        self._messages: list[dict] = []

    def reset(self) -> None:
        """清空计划对话历史（每次重新开启计划模式时调用）。"""
        self._messages = []

    def run(
        self,
        context_messages: list[dict],
        user_task: str,
    ) -> tuple[str, Optional[Path]]:
        """执行计划 Agent 循环。

        首次调用：以主 Agent 历史为上下文 + 新任务。
        后续调用（继续补充）：直接追加新任务到已有计划对话。

        Returns:
            (plan_text, plan_file_path)，plan_file_path 为 None 表示未生成
        """
        self._cancel.clear()
        system = PLAN_SYSTEM_PROMPT + f"\n当前工作目录: {self.cwd}"

        if not self._messages:
            # 首次：以主 Agent 完整历史为上下文
            self._messages = list(context_messages) + [
                {"role": "user", "content": user_task}
            ]
        else:
            # 继续补充：追加新消息到已有计划对话
            self._messages.append({"role": "user", "content": user_task})

        messages = self._messages

        final_text = ""
        MAX_ITER = 15
        for _ in range(MAX_ITER):
            if self._cancel.is_set():
                break

            response = self.llm.stream_chat(
                messages=messages,
                system=system,
                tools=self._tools,
                on_text=self._on_text,
                cancel_event=self._cancel,
            )

            if response.text_content:
                print()

            if self._cancel.is_set():
                break

            # 构建 assistant 消息块
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
                messages.append({"role": "assistant", "content": content_blocks})

            if not response.tool_calls:
                break

            # 执行只读工具（自动允许，无需权限确认）
            tool_results = self._execute_tools(response.tool_calls)
            messages.append({"role": "user", "content": tool_results})

        # 保存计划文件
        plan_file: Optional[Path] = None
        if final_text.strip():
            plan_file = self._save_plan(user_task, final_text)

        return final_text, plan_file

    def _execute_tools(self, tool_calls: list[ToolCall]) -> list[dict]:
        """串行执行只读工具，自动允许，无权限弹窗。"""
        results: list[dict] = []

        for tc in tool_calls:
            self._render_tool_call(tc.name, tc.input)
            try:
                tool = ToolRegistry.instantiate(tc.name)
                result = tool.execute(**tc.input)
                content = result.content or ""
                is_error = result.is_error
            except Exception as e:
                content = f"工具执行异常: {e}"
                is_error = True

            results.append({
                "type": "tool_result",
                "tool_use_id": tc.tool_use_id,
                "content": content,
                "is_error": is_error,
            })

        return results

    def _on_text(self, delta: str) -> None:
        print(delta, end="", flush=True)

    def _render_tool_call(self, tool_name: str, args: dict) -> None:
        args_preview = "\n".join(
            f"  {k}: {str(v)[:120]}" for k, v in list(args.items())[:5]
        )
        self.console.print(Panel(
            args_preview or "(无参数)",
            title=f"[bold cyan][计划] → {tool_name}[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))

    def _save_plan(self, task: str, plan_text: str) -> Path:
        """保存计划 Markdown 到 .myvibe/plans/<timestamp>.md。"""
        self._plan_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_file = self._plan_dir / f"plan_{ts}.md"
        content = (
            f"# 执行计划\n\n"
            f"**任务**: {task}\n"
            f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**工作目录**: {self.cwd}\n\n"
            f"---\n\n"
            f"{plan_text}\n"
        )
        plan_file.write_text(content, encoding="utf-8")
        return plan_file
