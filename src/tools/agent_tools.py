"""Agent 相关工具：spawn_agent（创建子代理）。

spawn_agent 不直接注册为 ToolRegistry 工具（因为它需要访问父 Agent 状态），
而是作为工具 schema 提供给 LLM，由 CodingAgent.handle_tool_calls() 特殊处理。
"""
from __future__ import annotations

# spawn_agent 的 Anthropic 工具 schema
SPAWN_AGENT_SCHEMA: dict = {
    "name": "spawn_agent",
    "description": (
        "创建一个独立的子代理来完成特定子任务。"
        "子代理拥有独立的上下文，不会干扰当前对话历史。\n\n"
        "适用场景：\n"
        "- 需要将大任务分解为独立子任务并行处理\n"
        "- 执行独立的文件重构、代码搜索等操作\n"
        "- 需要隔离执行，防止中间过程污染主上下文\n\n"
        "子代理可以使用所有工具（read_file、write_file、shell 等），"
        "但不能再次调用 spawn_agent。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "子代理需要完成的具体任务描述，越详细越好",
            },
            "context": {
                "type": "string",
                "description": "子代理需要的背景信息（文件路径、相关代码片段等），可选",
            },
        },
        "required": ["task"],
    },
}
