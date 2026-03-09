# -*- coding: utf-8 -*-
import os
import json
import time
from typing import List, Dict, Optional
from zai import ZhipuAiClient   # 假设你已经有的客户端封装

# ----------------------- 配置 -----------------------
API_KEY = "f94be6b452fa4eba8dd2a5d3941ca20f.XEA0sJoYknn0VhGJ"
MODEL = "glm-4.7"

client = ZhipuAiClient(api_key=API_KEY)

# 极简 tools（只保留一个最常用的，避免 tools schema 变化破坏缓存）
TOOLS = [
    {

        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取城市天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名，如 北京"}
                },
                "required": ["city"]
            }
        }
    }
]

# 固定不变的 system prompt（越长越容易看出缓存收益）
SYSTEM = """你是一个天气查询小助手。
用自然、友好的语气回答。
如果用户问天气，必须调用 get_weather 工具。
回答时要包含温度、天气状况。
"""

def count_tokens_approx(messages: List[Dict], system: str = "") -> int:
    """粗略估算，仅供参考"""
    text = system + "\n" + "\n".join([m["content"] or "" for m in messages if m.get("content")])
    return len(text) // 2 + len(messages) * 10   # 经验值

class SimpleAgent:
    def __init__(self):
        self.messages: List[Dict] = [{"role": "system", "content": SYSTEM}]
        self.turn = 0

    def chat(self, user_input: str):
        self.turn += 1
        print(f"\n========== Turn {self.turn} ==========")
        print(f"User: {user_input}")

        self.messages.append({"role": "user", "content": user_input})

        estimated = count_tokens_approx(self.messages[1:], SYSTEM)  # 不含 system 再加回来
        print(f"Estimated input tokens (approx): {estimated}")

        start = time.time()

        # 核心：调用流式接口（你的 _stream_chat_impl 逻辑类似这里）
        response = client.chat.completions.create(
            model=MODEL,
            messages=self.messages,
            tools=TOOLS,
            tool_choice="auto",
            stream=False   # 为方便观察，先用非流式；想看流式可改回 stream=True
        )

        elapsed = time.time() - start

        choice = response.choices[0]
        message = choice.message

        usage = response.usage
        cached = usage.prompt_tokens_details.get("cached_tokens", 0) if hasattr(usage, "prompt_tokens_details") else 0

        print(f"Response time: {elapsed:.2f}s")
        print(f"Prompt tokens : {usage.prompt_tokens}")
        print(f"Completion    : {usage.completion_tokens}")
        print(f"Cached tokens : {cached}  ({cached/usage.prompt_tokens:.1%} hit rate)")

        # 处理内容
        if message.tool_calls:
            print("→ 需要调用工具")
            for tc in message.tool_calls:
                func_name = tc.function.name
                args = json.loads(tc.function.arguments)
                print(f"  Tool: {func_name} | Args: {args}")

                # 模拟工具返回（实际项目中换成真调用）
                if func_name == "get_weather":
                    city = args.get("city", "未知")
                    tool_result = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": func_name,
                        "content": json.dumps({
                            "city": city,
                            "temp": "23°C",
                            "condition": "多云",
                            "humidity": "60%"
                        }, ensure_ascii=False)
                    }
                    self.messages.append(tool_result)
                    print(f"  ← 工具结果已追加到 messages")
        else:
            print("Assistant:", message.content.strip())
            self.messages.append({"role": "assistant", "content": message.content})

        print("=" * 40)


# ----------------------- 运行实验 -----------------------
if __name__ == "__main__":
    agent = SimpleAgent()

    questions = [
        "上海今天天气怎么样？",
        "那明天呢？",
        "北京和上海哪个更热？",
        "帮我查一下纽约的天气",
        "刚才说的上海是多少度来着？"   # 考察是否还能记住历史（缓存不影响记忆，但能省token）
    ]

    for q in questions:
        agent.chat(q)
        time.sleep(1.2)   # 避免太快被限流或 cache 没来得及持久化
实验结果输出
========== Turn 1 ==========
User: 上海今天天气怎么样？
Estimated input tokens (approx): 50
Response time: 1.34s
Prompt tokens : 204
Completion    : 62
Cached tokens : 42  (20.6% hit rate)
→ 需要调用工具
  Tool: get_weather | Args: {'city': '上海'}
  ← 工具结果已追加到 messages
========================================

========== Turn 2 ==========
User: 那明天呢？
Estimated input tokens (approx): 108
Response time: 3.97s
Prompt tokens : 238
Completion    : 130
Cached tokens : 201  (84.5% hit rate)
→ 需要调用工具
  Tool: get_weather | Args: {'city': '上海'}
  ← 工具结果已追加到 messages
========================================

========== Turn 3 ==========
User: 北京和上海哪个更热？
Estimated input tokens (approx): 168
Response time: 0.81s
Prompt tokens : 275
Completion    : 42
Cached tokens : 235  (85.5% hit rate)
→ 需要调用工具
  Tool: get_weather | Args: {'city': '北京'}
  ← 工具结果已追加到 messages
  Tool: get_weather | Args: {'city': '上海'}
  ← 工具结果已追加到 messages
========================================

========== Turn 4 ==========
User: 帮我查一下纽约的天气
Estimated input tokens (approx): 272
Response time: 0.81s
Prompt tokens : 339
Completion    : 33
Cached tokens : 272  (80.2% hit rate)
→ 需要调用工具
  Tool: get_weather | Args: {'city': '纽约'}
  ← 工具结果已追加到 messages
========================================

========== Turn 5 ==========
User: 刚才说的上海是多少度来着？
Estimated input tokens (approx): 334
Response time: 1.93s
Prompt tokens : 377
Completion    : 163
Cached tokens : 336  (89.1% hit rate)
Assistant: 根据刚才的查询，上海的温度是 **23°C**，天气状况是多云，湿度为60%。😊
========================================
这是我做的缓存命中实验，请你基于这个项目也做一个缓存命中实验，因为我觉得目前这个项目的缓存命中似乎存在问题