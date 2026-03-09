"""Agent 状态管理：运行时状态、会话持久化、历史压缩。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ai_coding_agent.src.llm.client import LLMClient

MODEL_MAX_TOKENS = 200_000  # claude-3/4 系列上下文窗口


@dataclass
class AgentState:
    """Agent 运行时状态。"""
    session_id: str
    messages: list[dict] = field(default_factory=list)
    read_files: set[str] = field(default_factory=set)
    cwd: str = "."
    turn: int = 0
    system_prompt: str = ""           # 最新一轮发给 LLM 的系统提示词
    # token 统计
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0   # 思考链 tokens（GLM thinking mode）
    total_cost_usd: float = 0.0
    # 最近一次 LLM 响应的 token（用于上下文比例计算）
    last_response_input_tokens: int = 0
    # 计划模式开关
    plan_mode: bool = False
    # 记忆工具调用追踪（统计本会话中 read_memory 实际注入上下文的量）
    memory_tool_calls: int = 0
    memory_tool_tokens: int = 0

    def append_user(self, content: str) -> None:
        """追加用户消息。"""
        self.messages.append({"role": "user", "content": content})
        self.turn += 1

    def append_assistant(self, content: list) -> None:
        """追加助手消息（含 tool_use blocks）。"""
        self.messages.append({"role": "assistant", "content": content})

    def append_tool_results(self, results: list[dict]) -> None:
        """追加 tool_result 消息（role=user，多个结果合并为一条）。"""
        self.messages.append({"role": "user", "content": results})

    def get_context_ratio(self, model_max_tokens: int = MODEL_MAX_TOKENS) -> float:
        """返回当前估算 token / 最大 token 的比例。"""
        return self.last_response_input_tokens / model_max_tokens if model_max_tokens else 0.0

    def mark_file_read(self, file_path: str) -> None:
        """记录已读文件路径。"""
        self.read_files.add(file_path)

    def is_file_read(self, file_path: str) -> bool:
        """检查文件是否已在本会话读取过。"""
        return file_path in self.read_files

    def update_usage(self, input_tokens: int, output_tokens: int, cost_usd: float = 0.0, reasoning_tokens: int = 0) -> None:
        """累加 token 使用量和费用（含思考链 tokens）。"""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_reasoning_tokens += reasoning_tokens
        self.total_cost_usd += cost_usd
        self.last_response_input_tokens = input_tokens

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "read_files": list(self.read_files),
            "cwd": self.cwd,
            "turn": self.turn,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_reasoning_tokens": self.total_reasoning_tokens,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentState":
        state = cls(session_id=data["session_id"])
        state.messages = data.get("messages", [])
        state.system_prompt = data.get("system_prompt", "")
        state.read_files = set(data.get("read_files", []))
        state.cwd = data.get("cwd", ".")
        state.turn = data.get("turn", 0)
        state.total_input_tokens = data.get("total_input_tokens", 0)
        state.total_output_tokens = data.get("total_output_tokens", 0)
        state.total_cost_usd = data.get("total_cost_usd", 0.0)
        return state


class SessionManager:
    """会话持久化管理器：JSONL 存储，支持恢复、列出和压缩。"""

    def __init__(self, sessions_dir: str = ".agent_sessions") -> None:
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def save(self, state: AgentState) -> None:
        """覆盖写入最新状态快照（文件始终只保留最后一条）。"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "state": state.to_dict(),
        }
        path = self._session_path(state.session_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> Optional[AgentState]:
        """从 JSONL 取最后一条快照重建 state。"""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        last_line = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    last_line = line
        except Exception:
            return None

        if not last_line:
            return None
        try:
            record = json.loads(last_line)
            return AgentState.from_dict(record["state"])
        except Exception:
            return None

    def list_sessions(self) -> list[dict]:
        """列出所有会话的摘要信息。"""
        sessions = []
        for path in sorted(self.sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = path.stem
            try:
                state = self.load(session_id)
                if state:
                    sessions.append({
                        "session_id": session_id,
                        "turn": state.turn,
                        "messages": len(state.messages),
                        "cost_usd": state.total_cost_usd,
                        "cwd": state.cwd,
                        "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
            except Exception:
                pass
        return sessions

    def compress_history(
        self,
        state: AgentState,
        llm_client: "LLMClient",
        keep_recent: int = 10,
    ) -> AgentState:
        """用 LLM 压缩历史消息，保留最近 N 轮。

        Args:
            state: 当前会话状态
            llm_client: LLM 客户端（用于生成摘要）
            keep_recent: 保留最近 N 条消息

        Returns:
            压缩后的新 AgentState
        """
        messages = state.messages
        if len(messages) <= keep_recent:
            return state

        old_messages = messages[:-keep_recent]
        recent_messages = messages[-keep_recent:]

        # 构建压缩请求
        history_text = json.dumps(old_messages, ensure_ascii=False, indent=2)[:20000]
        compress_prompt = (
            "请将以下对话历史压缩为一段简洁的摘要（中文），"
            "保留所有重要的代码修改、决策和发现：\n\n" + history_text
        )

        try:
            response = llm_client.stream_chat(
                messages=[{"role": "user", "content": compress_prompt}],
                system="你是一个对话历史压缩助手。请生成简洁准确的历史摘要。",
                tools=[],
            )
            summary_text = response.text_content
        except Exception:
            # 压缩失败时直接截断
            summary_text = f"[历史已截断，原有 {len(old_messages)} 条消息]"

        # 构建压缩后的摘要消息
        summary_message = {
            "role": "user",
            "content": f"[历史摘要]\n{summary_text}",
        }
        summary_reply = {
            "role": "assistant",
            "content": [{"type": "text", "text": "已了解历史上下文。"}],
        }

        new_state = AgentState(session_id=state.session_id)
        new_state.messages = [summary_message, summary_reply] + recent_messages
        new_state.read_files = state.read_files
        new_state.cwd = state.cwd
        new_state.turn = state.turn
        new_state.total_input_tokens = state.total_input_tokens
        new_state.total_output_tokens = state.total_output_tokens
        new_state.total_cost_usd = state.total_cost_usd
        return new_state

    def fork_session(
        self,
        session_id: str,
        new_id: Optional[str] = None,
    ) -> Optional[AgentState]:
        """复制会话到新 ID。"""
        state = self.load(session_id)
        if not state:
            return None
        new_id = new_id or str(uuid.uuid4())[:8]
        state.session_id = new_id
        self.save(state)
        return state
