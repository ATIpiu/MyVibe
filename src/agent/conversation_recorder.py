"""对话完整记录器：在 Agent 层捕获每轮对话的所有细节。

每调用一次 run_turn，追加一条 JSON 到 JSONL 文件。
记录内容：
  - 用户输入（每个字符）
  - 系统提示词
  - 每次 LLM 调用的完整输出文本
  - 每个工具调用（名称、完整参数、完整结果、是否报错、耗时）
  - 最终回复文本、token 用量、总耗时
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ConversationRecorder:
    """单会话对话记录器。

    用法（在 CodingAgent 中）：
        recorder.start_turn(turn, user_input, system)
        recorder.start_llm_iteration()
        recorder.set_llm_response(text, tool_calls, ...)
        recorder.record_tool_result(tool_use_id, name, content, is_error, elapsed_ms)
        recorder.end_turn(final_text)
    """

    def __init__(self, output_file: str, session_id: str) -> None:
        self._path = Path(output_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._current: Optional[dict] = None  # 当前轮次的数据

    # ── 轮次控制 ─────────────────────────────────────────────────────────────

    def start_turn(self, turn: int, user_input: str, system: str) -> None:
        """开始新一轮对话，记录用户输入和系统提示词。"""
        self._current = {
            "session_id": self._session_id,
            "turn": turn,
            "start_time": _now_iso(),
            "user_input": user_input,          # 完整用户输入
            "system_prompt": system,           # 完整系统提示词
            "llm_iterations": [],              # 本轮每次 LLM 调用
            "final_text": "",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "total_elapsed_ms": 0,
            "end_time": "",
        }

    def start_llm_iteration(self) -> None:
        """开始一次新的 LLM 调用（一轮可能有多次，每次调用后再处理工具）。"""
        if self._current is None:
            return
        self._current["llm_iterations"].append({
            "llm_text": "",           # LLM 完整输出文本
            "stop_reason": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "elapsed_ms": 0,
            "tool_calls": [],         # 本次调用中的工具调用列表
        })

    def set_llm_response(
        self,
        text: str,
        tool_calls: list,          # list[ToolCall]
        stop_reason: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        elapsed_ms: int,
    ) -> None:
        """记录一次 LLM 响应的完整信息。"""
        if not self._current or not self._current["llm_iterations"]:
            return
        it = self._current["llm_iterations"][-1]
        it["llm_text"] = text
        it["stop_reason"] = stop_reason
        it["input_tokens"] = input_tokens
        it["output_tokens"] = output_tokens
        it["cost_usd"] = cost_usd
        it["elapsed_ms"] = elapsed_ms
        # 预填工具调用槽位（结果稍后由 record_tool_result 填入）
        it["tool_calls"] = [
            {
                "tool_use_id": tc.tool_use_id,
                "name": tc.name,
                "input": tc.input,   # 完整参数
                "result": None,      # 等待填入
                "is_error": False,
                "elapsed_ms": 0,
            }
            for tc in tool_calls
        ]
        # 累加统计
        self._current["total_input_tokens"] += input_tokens
        self._current["total_output_tokens"] += output_tokens
        self._current["total_cost_usd"] += cost_usd
        self._current["total_elapsed_ms"] += elapsed_ms

    def record_tool_result(
        self,
        tool_use_id: str,
        result_content: str,
        is_error: bool,
        elapsed_ms: int,
    ) -> None:
        """填写工具调用结果（完整 content）。"""
        if not self._current or not self._current["llm_iterations"]:
            return
        it = self._current["llm_iterations"][-1]
        for tc in it["tool_calls"]:
            if tc["tool_use_id"] == tool_use_id:
                tc["result"] = result_content   # 完整工具返回内容
                tc["is_error"] = is_error
                tc["elapsed_ms"] = elapsed_ms
                break

    def end_turn(self, final_text: str) -> None:
        """结束本轮，将完整记录追加写入 JSONL 文件。"""
        if self._current is None:
            return
        self._current["final_text"] = final_text
        self._current["end_time"] = _now_iso()
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._current, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[ConversationRecorder] 写入失败: {exc}", flush=True)
        self._current = None

    @property
    def output_path(self) -> Path:
        return self._path
