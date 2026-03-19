# Plan 04 - 后台任务管理系统

**解决 TODO**: 22
**优先级**: P1
**参考**: `learn-claude-code/s07_task_system.py`, `s08_background_tasks.py`

---

## 目标

支持将长时间运行的工具调用转入后台执行，主线程继续响应用户输入；提供 `/tasks` 和 `/task <id>` 命令查看任务状态。

---

## 任务状态机

```
pending → running → completed
                 → failed
         → cancelled
```

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tasks/__init__.py` | 新建 | 模块初始化 |
| `src/tasks/task_manager.py` | 新建 | 任务管理器 |
| `src/tasks/background_worker.py` | 新建 | 后台工作线程 |
| `src/tasks/task_model.py` | 新建 | Task 数据模型 |
| `src/agent/coding_agent.py` | 修改 | 集成后台任务 |
| `src/main.py` | 修改 | 注册 /tasks 斜杠命令 |

---

## 实现步骤

### Step 1: 创建任务模型 `src/tasks/task_model.py`

```python
import uuid
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

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

    def status_icon(self) -> str:
        icons = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.CANCELLED: "🚫",
        }
        return icons.get(self.status, "?")

    def summary(self) -> str:
        duration = self.duration()
        dur_str = f" ({duration:.1f}s)" if duration else ""
        return f"[{self.id}] {self.status_icon()} {self.name}{dur_str}"
```

### Step 2: 创建任务管理器 `src/tasks/task_manager.py`

```python
import threading
from typing import Callable, Optional
from src.tasks.task_model import Task, TaskStatus

class TaskManager:
    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create_task(self, name: str, description: str = "") -> Task:
        task = Task(name=name, description=description)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def active_tasks(self) -> list[Task]:
        return [t for t in self._tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]

    def submit(self, name: str, func: Callable, *args,
               description: str = "", **kwargs) -> Task:
        """提交后台任务，立即返回 Task 对象"""
        task = self.create_task(name, description)

        def run():
            import time
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

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return task

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.status = TaskStatus.CANCELLED
            return True
        return False

    def format_list(self) -> str:
        tasks = self.list_tasks()
        if not tasks:
            return "没有任务"
        return "\n".join(t.summary() for t in tasks)

    def format_detail(self, task_id: str) -> str:
        task = self.get(task_id)
        if not task:
            return f"任务 {task_id} 不存在"

        lines = [
            f"任务 ID: {task.id}",
            f"名称: {task.name}",
            f"状态: {task.status_icon()} {task.status.value}",
        ]
        if task.description:
            lines.append(f"描述: {task.description}")
        if task.duration():
            lines.append(f"耗时: {task.duration():.1f}s")
        if task.result:
            lines.append(f"结果: {task.result}")
        if task.error:
            lines.append(f"错误: {task.error}")
        if task.output_lines:
            lines.append("输出:")
            lines.extend(f"  {line}" for line in task.output_lines[-20:])

        return "\n".join(lines)


# 全局单例
_manager = TaskManager()

def get_task_manager() -> TaskManager:
    return _manager
```

### Step 3: 注册斜杠命令 `src/main.py`

```python
from src.tasks.task_manager import get_task_manager

def handle_slash_command(cmd: str, args: str) -> Optional[str]:
    manager = get_task_manager()

    if cmd == "/tasks":
        return manager.format_list()

    if cmd == "/task":
        if not args:
            return "用法：/task <task_id>"
        return manager.format_detail(args.strip())

    if cmd == "/cancel":
        task_id = args.strip()
        if manager.cancel(task_id):
            return f"已取消任务 {task_id}"
        return f"无法取消任务 {task_id}（不存在或已结束）"

    return None
```

### Step 4: 集成到工具执行器

对于耗时工具（如代码执行、大文件处理），提供后台执行选项：

```python
# 在 tool_executor.py 中
from src.tasks.task_manager import get_task_manager

def execute_in_background(tool_name: str, tool_input: dict) -> str:
    manager = get_task_manager()
    task = manager.submit(
        name=f"工具: {tool_name}",
        func=self.execute,
        tool_name, tool_input,
        description=str(tool_input)[:100]
    )
    return f"任务已提交后台，ID: {task.id}。使用 /tasks 查看状态。"
```

---

## 验证方法

1. 提交一个长时间工具调用到后台
2. 主线程继续正常响应用户输入
3. 使用 `/tasks` 查看任务列表，状态正确更新
4. 任务完成后 `/task <id>` 显示结果
5. 测试 `/cancel <id>` 取消进行中的任务
6. 测试任务失败时显示正确错误信息

---

## 注意事项

- 后台线程使用 daemon=True，主进程退出时自动终止
- 任务数量应有上限（如 100 个），超出时清理最早已完成的任务
- 后台任务的 stdout 输出应捕获到 output_lines，不直接打印到终端
