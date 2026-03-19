"""后台任务数据模型。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_STATUS_ICON = {
    TaskStatus.PENDING:   "⏳",
    TaskStatus.RUNNING:   "🔄",
    TaskStatus.COMPLETED: "✅",
    TaskStatus.FAILED:    "❌",
    TaskStatus.CANCELLED: "🚫",
}


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    output_lines: list[str] = field(default_factory=list)

    def duration(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        if self.started_at:
            return time.time() - self.started_at
        return None

    @property
    def icon(self) -> str:
        return _STATUS_ICON.get(self.status, "?")

    def summary(self) -> str:
        dur = self.duration()
        dur_str = f" ({dur:.1f}s)" if dur is not None else ""
        return f"[{self.id}] {self.icon} {self.name}{dur_str}  {self.status.value}"

    def detail(self) -> str:
        lines = [
            f"ID:      {self.id}",
            f"名称:    {self.name}",
            f"状态:    {self.icon} {self.status.value}",
        ]
        if self.description:
            lines.append(f"描述:    {self.description}")
        dur = self.duration()
        if dur is not None:
            lines.append(f"耗时:    {dur:.1f}s")
        if self.result:
            lines.append(f"结果:    {str(self.result)[:200]}")
        if self.error:
            lines.append(f"错误:    {self.error}")
        if self.output_lines:
            lines.append("输出:")
            lines.extend(f"  {line}" for line in self.output_lines[-20:])
        return "\n".join(lines)
