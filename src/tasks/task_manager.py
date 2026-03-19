"""后台任务管理器：提交、查询、取消任务。"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .task_model import Task, TaskStatus

_MAX_TASKS = 100  # 保留最近 N 个任务（超出时清理最早已完成的）


class TaskManager:
    """线程安全的任务管理器。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    # ── 提交与执行 ──────────────────────────────────────────────────────

    def submit(
        self,
        name: str,
        func: Callable,
        *args,
        description: str = "",
        **kwargs,
    ) -> Task:
        """提交一个后台任务，立即返回 Task 对象（非阻塞）。"""
        task = Task(name=name, description=description)
        with self._lock:
            self._tasks[task.id] = task
            self._gc_if_needed()

        def _run() -> None:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            try:
                result = func(*args, **kwargs)
                task.result = result
                task.status = TaskStatus.COMPLETED
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.FAILED
            finally:
                task.finished_at = time.time()

        t = threading.Thread(target=_run, daemon=True, name=f"bg-task-{task.id}")
        t.start()
        return task

    # ── 查询 ─────────────────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def active_tasks(self) -> list[Task]:
        return self.list_tasks(TaskStatus.RUNNING) + self.list_tasks(TaskStatus.PENDING)

    # ── 取消 ─────────────────────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """标记任务为已取消（不能强制终止线程）。"""
        task = self.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            return True
        return False

    # ── 格式化输出 ────────────────────────────────────────────────────────

    def format_list(self) -> str:
        tasks = self.list_tasks()
        if not tasks:
            return "暂无任务"
        return "\n".join(t.summary() for t in tasks)

    def format_detail(self, task_id: str) -> str:
        task = self.get(task_id)
        if not task:
            return f"任务 {task_id} 不存在"
        return task.detail()

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _gc_if_needed(self) -> None:
        """超过上限时清理最早的已完成任务（调用方持锁）。"""
        if len(self._tasks) <= _MAX_TASKS:
            return
        done_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        done = sorted(
            [t for t in self._tasks.values() if t.status in done_statuses],
            key=lambda t: t.created_at,
        )
        for task in done[: len(self._tasks) - _MAX_TASKS]:
            del self._tasks[task.id]


# ── 全局单例 ───────────────────────────────────────────────────────────────────

_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
