import zai

from zai import ZhipuAiClient

client = ZhipuAiClient(api_key='f94be6b452fa4eba8dd2a5d3941ca20f.XEA0sJoYknn0VhGJ')


response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "user", "content": "你好"},

    ],
    thinking={
        "type": "enabled",    # 启用深度思考模式
    },
    max_tokens=65536,          # 最大输出 tokens
    temperature=1.0           # 控制输出的随机性
)

# 获取完整回复
print(response.choices[0].message)
# 初始化客户端