# Plan 02 - 子 Agent 系统

**解决 TODO**: 12
**优先级**: P0（推荐优先实施）
**参考**: `learn-claude-code/s04_subagent.py`

---

## 目标

实现上下文隔离的子代理，使主 Agent 可以将子任务委托给独立的子 Agent 执行，子 Agent 拥有独立的消息历史和工具调用。

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/agent/sub_agent.py` | 新建 | 子代理核心实现 |
| `src/tools/agent_tools.py` | 新建 | spawn_agent 工具定义 |
| `src/agent/coding_agent.py` | 修改 | 注册 spawn_agent 工具 |
| `src/agent/tool_executor.py` | 修改 | 处理 spawn_agent 调用 |

---

## 实现步骤

### Step 1: 创建子代理 `src/agent/sub_agent.py`

参考 `learn-claude-code/s04_subagent.py` 的核心模式：

```python
import anthropic
from typing import Optional

class SubAgent:
    """上下文隔离的子代理，拥有独立的消息历史"""

    def __init__(self, client: anthropic.Anthropic, model: str,
                 system_prompt: str, tools: list = None):
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.messages = []  # 独立的消息历史，与父 Agent 完全隔离

    def run(self, task: str, max_iterations: int = 10) -> str:
        """运行子代理完成任务，返回最终结果"""
        self.messages.append({"role": "user", "content": task})

        for _ in range(max_iterations):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=self.tools,
                messages=self.messages,
            )

            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # 提取文本结果
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                tool_results = self._execute_tools(response.content)
                self.messages.append({"role": "user", "content": tool_results})

        return "子代理达到最大迭代次数"

    def _execute_tools(self, content_blocks) -> list:
        """执行工具调用，返回工具结果列表"""
        results = []
        for block in content_blocks:
            if block.type == "tool_use":
                # 调用实际工具执行器
                result = self._dispatch_tool(block.name, block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })
        return results

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        """分发工具调用（子类可重写以支持不同工具集）"""
        raise NotImplementedError("子类需实现工具分发逻辑")


class CodingSubAgent(SubAgent):
    """专门用于代码任务的子代理"""

    def __init__(self, client, model, parent_tools_executor):
        system = (
            "你是一个专注于代码任务的子代理。完成指定的编码任务后，"
            "用清晰的文字总结你完成的工作和结果。"
        )
        super().__init__(client, model, system)
        self.executor = parent_tools_executor

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        return self.executor.execute(tool_name, tool_input)
```

### Step 2: 创建 spawn_agent 工具 `src/tools/agent_tools.py`

```python
SPAWN_AGENT_TOOL = {
    "name": "spawn_agent",
    "description": (
        "创建一个独立的子代理来完成特定子任务。子代理拥有独立的上下文，"
        "不会干扰当前对话。适用于：并行处理多个文件、隔离执行风险操作、"
        "将大任务分解为独立子任务。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "子代理需要完成的具体任务描述",
            },
            "context": {
                "type": "string",
                "description": "子代理需要的上下文信息（可选）",
            },
            "agent_type": {
                "type": "string",
                "enum": ["coding", "research", "general"],
                "description": "子代理类型，默认为 general",
            },
        },
        "required": ["task"],
    },
}
```

### Step 3: 修改 `src/agent/tool_executor.py`

在工具分发中增加 `spawn_agent` 的处理：

```python
elif tool_name == "spawn_agent":
    task = tool_input.get("task", "")
    context = tool_input.get("context", "")
    agent_type = tool_input.get("agent_type", "general")

    full_task = f"{context}\n\n任务：{task}" if context else task

    sub_agent = CodingSubAgent(self.client, self.model, self)
    result = sub_agent.run(full_task)
    return result
```

### Step 4: 修改 `src/agent/coding_agent.py`

将 `SPAWN_AGENT_TOOL` 加入工具列表：

```python
from src.tools.agent_tools import SPAWN_AGENT_TOOL

# 在 build_tools() 中：
tools = [...existing_tools..., SPAWN_AGENT_TOOL]
```

---

## 验证方法

1. 让主 Agent 调用 `spawn_agent` 处理一个文件重构任务
2. 确认子代理消息历史与主对话完全隔离
3. 验证子代理的工具调用（读文件、写文件）正常工作
4. 测试子代理完成任务后结果正确返回给主 Agent
5. 测试错误情况：子代理超过最大迭代次数时的优雅退出

---

## 注意事项

- 子代理的工具集应受限（不允许 spawn_agent，防止无限递归）
- 记录子代理的执行日志，便于调试
- 子代理的 token 消耗计入总费用，应设置合理的 max_iterations
