# Plan 01 - 多模型路由与 VLM 支持

**解决 TODO**: 5, 6, 7, 8
**优先级**: P2
**依赖**: 无

---

## 目标

根据任务类型自动路由到最合适的模型，降低成本同时保持质量；支持图片输入，自动切换到 VLM 模型。

---

## 上下文管理策略

### 核心结论：主对话上下文跨模型共享

Anthropic API 是无状态的，每次调用显式传入完整 `messages` 数组，模型不持有状态。因此切换模型只需把同一个 `messages` 传给不同的 `model_id`：

```
用户问简单问题 → Haiku(messages) → 追加回复到 messages
用户问复杂问题 → Opus(messages)  → 追加回复到 messages
                    ↑ 同一个 messages 数组，跨模型共享
```

### 三类上下文的隔离原则

| 场景 | 上下文 | 原因 |
|------|--------|------|
| 主对话切换模型 | **共享** | 同一 messages 数组，只换 model_id |
| 子 Agent（Plan 02） | **隔离** | fork() 创建空上下文，防止上下文污染和 token 浪费 |
| 计划模式（Plan 10） | **共享** | 用户可见推理过程，保持连贯性 |

### 需注意的副作用

1. **Cache 失效**：prompt cache 绑定到特定模型，切换模型导致 cache miss，长 system prompt 会重复计费。
   - 建议：同一会话内尽量固定模型，只在"任务边界"（新对话）时切换。

2. **Context Window**：当前 Claude 系列均为 200K，暂无截断问题。引入更小模型时需加 `fits_in_context()` 检查。

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/llm/model_router.py` | 新建 | 模型路由核心逻辑 |
| `src/llm/conversation_context.py` | 新建 | 跨模型共享的上下文管理器 |
| `src/llm/client.py` | 修改 | 集成路由器 |
| `src/agent/coding_agent.py` | 修改 | 支持任务类型标注 |
| `src/tools/file_tools.py` | 修改 | 图片文件检测 |

---

## 实现步骤

### Step 1: 创建上下文管理器 `src/llm/conversation_context.py`

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ConversationContext:
    """跨模型共享的对话上下文，管理 messages 数组"""
    messages: list[dict] = field(default_factory=list)

    def add(self, role: str, content: Any):
        """追加一条消息"""
        self.messages.append({"role": role, "content": content})

    def for_model(self, model_id: str) -> list[dict]:
        """
        返回适合该模型的 messages 列表。
        当前直接返回全部；未来引入小模型时在此做截断。
        """
        # TODO: 当 model 上下文窗口较小时，截取最近 N 条消息
        return self.messages

    def fork(self) -> 'ConversationContext':
        """
        为子 Agent 创建隔离的空上下文。
        不复制历史，防止主对话噪音污染子任务。
        """
        return ConversationContext()

    def token_estimate(self) -> int:
        """粗略估算当前 messages 的 token 数（4字符≈1token）"""
        total = sum(len(str(m.get("content", ""))) for m in self.messages)
        return total // 4
```

### Step 2: 创建模型路由器 `src/llm/model_router.py`

```python
from enum import Enum
from dataclasses import dataclass

class TaskType(Enum):
    SIMPLE_CHAT = "simple_chat"        # 普通聊天，使用廉价模型
    CODE_WRITE = "code_write"          # 代码编写，使用中等模型
    CODE_ANALYSIS = "code_analysis"    # 代码分析/审查，使用高端模型
    PLAN = "plan"                      # 计划制定，使用高端模型
    VLM = "vlm"                        # 视觉任务，使用 VLM 模型

@dataclass
class ModelConfig:
    model_id: str
    max_tokens: int
    context_window: int                # 该模型支持的最大上下文 token 数
    description: str

MODEL_MAP = {
    TaskType.SIMPLE_CHAT:   ModelConfig("claude-haiku-4-5-20251001", 2048,  200_000, "快速响应"),
    TaskType.CODE_WRITE:    ModelConfig("claude-sonnet-4-6",         8192,  200_000, "代码生成"),
    TaskType.CODE_ANALYSIS: ModelConfig("claude-opus-4-6",           8192,  200_000, "深度分析"),
    TaskType.PLAN:          ModelConfig("claude-opus-4-6",           4096,  200_000, "计划制定"),
    TaskType.VLM:           ModelConfig("claude-sonnet-4-6",         4096,  200_000, "视觉理解"),
}

def detect_task_type(messages: list, has_images: bool = False) -> TaskType:
    """根据消息内容自动检测任务类型"""
    if has_images:
        return TaskType.VLM

    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            break

    text = last_user_msg.lower()

    plan_keywords = ["制定计划", "分析架构", "设计方案", "规划", "plan", "design", "architecture"]
    if any(k in text for k in plan_keywords):
        return TaskType.PLAN

    code_keywords = ["写代码", "实现", "修复", "重构", "write", "implement", "fix", "refactor"]
    if any(k in text for k in code_keywords):
        return TaskType.CODE_WRITE

    return TaskType.SIMPLE_CHAT

def detect_has_images(messages: list) -> bool:
    """检测最后一条用户消息是否包含图片"""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                return any(block.get("type") == "image" for block in content)
            break
    return False

def route_model(messages: list, has_images: bool = False,
                force_task: TaskType = None) -> ModelConfig:
    task = force_task or detect_task_type(messages, has_images)
    return MODEL_MAP[task]
```

### Step 3: 修改 `src/llm/client.py`

```python
from src.llm.model_router import route_model, detect_has_images
from src.llm.conversation_context import ConversationContext

class LLMClient:
    def send(self, ctx: ConversationContext, system: str, tools: list,
             force_task=None) -> ...:
        has_images = detect_has_images(ctx.messages)
        config = route_model(ctx.messages, has_images, force_task)

        # 根据模型容量获取适合的 messages（未来截断逻辑在此生效）
        messages = ctx.for_model(config.model_id)

        print(f"[router] 使用模型: {config.model_id} ({config.description})")

        return self.client.messages.create(
            model=config.model_id,
            max_tokens=config.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
```

### Step 4: 图片输入支持 `src/tools/file_tools.py`

```python
import base64
from pathlib import Path

SUPPORTED_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"
}

def read_image_as_base64(file_path: str) -> dict:
    """读取图片文件，返回 Anthropic API 的 image content block"""
    path = Path(file_path)
    media_type = SUPPORTED_IMAGE_TYPES.get(path.suffix.lower())
    if not media_type:
        raise ValueError(f"不支持的图片格式: {path.suffix}")

    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data}
    }
```

### Step 5: 修改 `src/agent/coding_agent.py`

- 将 `messages: list` 替换为 `ctx: ConversationContext`
- `build_system_prompt()` 接受可选的 `task_type` 参数，根据任务类型调整侧重点
- Agent 循环中通过 `ctx.add()` 追加消息，通过 `ctx.fork()` 创建子 Agent 上下文

---

## 验证方法

1. 发送普通聊天消息 → 日志显示 `[router] 使用模型: claude-haiku-*`
2. 发送"帮我写一个函数" → 日志显示 Sonnet
3. 发送"制定架构计划" → 日志显示 Opus
4. 附加图片后发送 → 日志显示 VLM 模型
5. 多轮对话中切换任务类型 → 确认 messages 历史完整保留（上下文共享验证）
6. 对比成本：10次简单问答，Haiku vs Opus 费用差异

---

## 注意事项

- `ConversationContext.fork()` 返回空上下文，**不继承**主对话历史，子 Agent 专用
- 路由逻辑保持简单，避免过度工程化
- 用户可通过 `--model` 参数传入 `force_task` 覆盖自动路由
- 每次请求记录使用的模型到日志，便于成本分析
- 切换模型会导致 cache miss，频繁切换时留意 API 费用
