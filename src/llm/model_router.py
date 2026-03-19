"""GLM 模型路由器。

根据用户消息内容自动选择最合适的 GLM 模型，平衡成本与质量：
  - glm-4.7-flash : 免费，适合简单问答
  - glm-4.7       : 标准，适合代码编写
  - glm-5         : 高端，适合深度分析与计划

使用方式（在 config.yaml 中启用）：
    agent:
      model_routing: true

集成点：CodingAgent.run_turn() 开头调用 route_model()，临时覆盖 self.llm.model。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskType(Enum):
    SIMPLE_CHAT = "simple_chat"      # 普通聊天、问答
    CODE_WRITE = "code_write"        # 代码编写、实现
    CODE_ANALYSIS = "code_analysis"  # 代码审查、深度分析
    PLAN = "plan"                    # 计划制定、架构设计


@dataclass
class ModelConfig:
    model_id: str
    max_tokens: int
    description: str


# GLM 模型路由表
# glm-4.7-flash 免费，glm-4.7 标准，glm-5 高端
MODEL_MAP: dict[TaskType, ModelConfig] = {
    TaskType.SIMPLE_CHAT:   ModelConfig("glm-4.7-flash", 2048, "快速响应（免费）"),
    TaskType.CODE_WRITE:    ModelConfig("glm-4.7",        8192, "代码生成"),
    TaskType.CODE_ANALYSIS: ModelConfig("glm-5",          8192, "深度分析"),
    TaskType.PLAN:          ModelConfig("glm-5",          4096, "计划制定"),
}

# 任务类型检测关键词
_PLAN_KEYWORDS = [
    "制定计划", "分析架构", "设计方案", "规划", "整体设计",
    "plan", "architecture", "design", "roadmap",
]
_CODE_KEYWORDS = [
    "写代码", "实现", "修复", "重构", "编写", "开发", "添加功能",
    "write", "implement", "fix", "refactor", "create", "build", "develop",
]
_ANALYSIS_KEYWORDS = [
    "分析", "审查", "解释", "评估", "检查问题", "为什么", "原因",
    "review", "analyze", "explain", "evaluate", "why", "diagnose",
]


def detect_task_type(messages: list[dict]) -> TaskType:
    """根据最近一条用户消息的内容检测任务类型。"""
    last_user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            elif isinstance(content, list):
                # 提取文本 block
                last_user_msg = " ".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
            break

    text = last_user_msg.lower()

    if any(k in text for k in _PLAN_KEYWORDS):
        return TaskType.PLAN

    if any(k in text for k in _CODE_KEYWORDS):
        return TaskType.CODE_WRITE

    if any(k in text for k in _ANALYSIS_KEYWORDS):
        return TaskType.CODE_ANALYSIS

    return TaskType.SIMPLE_CHAT


def route_model(
    messages: list[dict],
    force_task: TaskType | None = None,
) -> ModelConfig:
    """返回适合当前任务的模型配置。

    Args:
        messages: 完整对话历史
        force_task: 强制指定任务类型（跳过自动检测）
    """
    task = force_task or detect_task_type(messages)
    return MODEL_MAP[task]
