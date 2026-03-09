import time
from zai import ZhipuAiClient

client = ZhipuAiClient(api_key='f94be6b452fa4eba8dd2a5d3941ca20f.XEA0sJoYknn0VhGJ')

tools =  [
    {
      'type': 'function',
      'function': {
        'name': 'read_memory',
        'description': "读取项目代码记忆。scope 控制读取粒度：\n- scope='all'：返回全项目所有模块及其函数列表（项目全览）\n- scope='modules'：返回指定模块的函数列表（需提供 modules 参数）\n- scope='function'：返回指定函数的完整源码 + 调用关系（需提供 function_key 参数）\nfunction_key 格式：'module_path:qualname'，类方法写法：'src/foo.py:MyClass.method'",
        'parameters': {
          'type': 'object',
          'properties': {
            'scope': {
              'type': 'string',
              'enum': [
                'all',
                'modules',
                'function'
              ],
              'description': '读取粒度：all=全项目 / modules=指定模块 / function=指定函数'
            },
            'modules': {
              'type': 'array',
              'items': {
                'type': 'string'
              },
              'description': "scope='modules' 时必填，模块路径列表，如 ['src/utils/path.py']"
            },
            'function_key': {
              'type': 'string',
              'description': "scope='function' 时必填，格式 'module_path:qualname'"
            }
          },
          'required': [
            'scope'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'rebuild_memory',
        'description': '初始化或重建项目代码记忆索引。\n- 不填 file_path：扫描整个项目，全量重建\n- 填写 file_path：只同步单个文件（适合刚修改某个文件后快速更新）\n注意：write_file / edit_file 会自动触发单文件同步，通常无需手动调用。',
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '可选，指定文件路径则只同步该文件；不填则全项目扫描'
            }
          }
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'git_status',
        'description': '显示 git 仓库工作区和暂存区状态（等同于 git status --short）',
        'parameters': {
          'type': 'object',
          'properties': {
            'path': {
              'type': 'string',
              'description': 'git 仓库路径，默认为当前目录',
              'default': '.'
            }
          }
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'git_diff',
        'description': '显示 git 差异。staged=true 显示已暂存改动，否则显示工作区未暂存改动。',
        'parameters': {
          'type': 'object',
          'properties': {
            'staged': {
              'type': 'boolean',
              'description': 'true 显示已暂存改动（git diff --staged），false 显示工作区改动',
              'default': False
            },
            'file': {
              'type': 'string',
              'description': '限定到特定文件（可选）'
            },
            'path': {
              'type': 'string',
              'description': 'git 仓库路径',
              'default': '.'
            }
          }
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'git_commit',
        'description': '将指定文件暂存（git add）并创建提交（git commit）。此操作需要用户权限 确认。',
        'parameters': {
          'type': 'object',
          'properties': {
            'message': {
              'type': 'string',
              'description': '提交消息'
            },
            'files': {
              'type': 'array',
              'items': {
                'type': 'string'
              },
              'description': '要提交的文件路径列表（相对于仓库根目录）'
            },
            'path': {
              'type': 'string',
              'description': 'git 仓库路径',
              'default': '.'
            }
          },
          'required': [
            'message',
            'files'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'read_file',
        'description': "读取文件内容，每行带行号前缀。支持分页（offset/limit）。返回格式：'行号→内容'，便于精确引用。",
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '要读取的 文件的绝对路径或相对路径'
            },
            'offset': {
              'type': 'integer',
              'description': '起始行号（从1开始），默认1',
              'default': 1
            },
            'limit': {
              'type': 'integer',
              'description': '最多读取行数，默认2000',
              'default': 2000
            }
          },
          'required': [
            'file_path'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'write_file',
        'description': '将内容完整写入文件（覆盖或新建）。自动创建所需的父目录。写入前请确认用户已同意覆盖已有文件。',
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '目标文件的绝对或相对路径'
            },
            'content': {
              'type': 'string',
              'description': '要写入的完整文件内容'
            }
          },
          'required': [
            'file_path',
            'content'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'edit_file',
        'description': '在文件中精确替换字符串。old_string 必须在文件中唯一出现（除非 replace_all=true）。如果 old_string 不唯一，工具会返回错误并说明出现次数，请提供更多上下文再试。',
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '要编辑的文件路径'
            },
            'old_string': {
              'type': 'string',
              'description': '要被替换的精确字符串（必须在文件中唯一出现）'
            },
            'new_string': {
              'type': 'string',
              'description': '替换后的新字符串'
            },
            'replace_all': {
              'type': 'boolean',
              'description': '是否替换所有出现（默认 false）',
              'default': False
            }
          },
          'required': [
            'file_path',
            'old_string',
            'new_string'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'search_in_file',
        'description': '在单个文件中用正则表达式搜索，返回匹配行及前后上下文行。',
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '要搜索的文件路径'
            },
            'pattern': {
              'type': 'string',
              'description': 'Python 正则表达式搜索模式'
            },
            'context': {
              'type': 'integer',
              'description': '每个匹配前后显示的上下文行数，默认2',
              'default': 2
            }
          },
          'required': [
            'file_path',
            'pattern'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'shell',
        'description': '在系统 shell 中执行命令。会进行注入检测和危险命令检查。危险命令需要用户确认。输出超过 10000 字符时自动截断。',
        'parameters': {
          'type': 'object',
          'properties': {
            'command': {
              'type': 'string',
              'description': '要执行的 shell 命令'
            },
            'timeout': {
              'type': 'integer',
              'description': '超时毫秒数，默认 120000（2分钟）',
              'default': 120000
            },
            'description': {
              'type': 'string',
              'description': '命令的人类可读描述（用于权限确认弹窗）',
              'default': ''
            },
            'working_dir': {
              'type': 'string',
              'description': '命令执行的工作目录（默认为 agent cwd）'
            }
          },
          'required': [
            'command'
          ]
        }
      }
    },
    {
      'type': 'function',
      'function': {
        'name': 'lsp_hover',
        'description': '获取指定文件位置的符号类型信息和文档（通过 LSP）。Phase1 为占位实现，Phase2 接入 pylsp。',
        'parameters': {
          'type': 'object',
          'properties': {
            'file_path': {
              'type': 'string',
              'description': '文件路径'
            },
            'line': {
              'type': 'integer',
              'description': '行号（从1开始）'
            },
            'character': {
              'type': 'integer',
              'description': '列号（从0开始）'
            }
          },
          'required': [
            'file_path',
            'line',
            'character'
          ]
        }
      }
    }
  ]

model = "glm-4.7"

# ------------------- 准备不同长度的稳定前缀 -------------------
system_prompt = "你是一个专业的代码审查助手，严格按照PEP8规范..."  # 长一点更好

# 场景1：冷启动（几乎无缓存）
messages_cold = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": "解释一下什么是KV cache？"}
]

resp1 = client.chat.completions.create(
    model=model,
    messages=messages_cold,
    tools=tools,
    tool_choice="auto",
    stream=False  # 先用非流式看完整usage
)
print("冷启动:", resp1.usage.prompt_tokens_details.cached_tokens)  # 预期 ≈ 0 或很小

# 场景2：第一次追加（开始积累缓存）
messages_2 = messages_cold + [
    {"role": "assistant", "content": resp1.choices[0].message.content},
    {"role": "user", "content": "那在多轮对话中它是怎么复用的？"}
]

resp2 = client.chat.completions.create(
    model=model,
    messages=messages_2,
    tools=tools,
    tool_choice="auto",
    stream=False   # 先用非流式看完整usage
)
print("第2轮:", resp2.usage.prompt_tokens_details.cached_tokens)

# 场景3~5：连续追加（应该看到cached_tokens越来越接近总prompt_tokens）
for i in range(3, 7):
    prev_content = resp2.choices[0].message.content  # 假设你把上轮assistant存下来
    new_user_msg = f"请继续解释第{i}点，结合工具调用场景。"

    messages_n = messages_2 + [  # 注意：一直累加，不要重新new list
        {"role": "assistant", "content": prev_content},
        {"role": "user", "content": new_user_msg}
    ]

    resp_n = client.chat.completions.create(
    model=model,
    messages=messages_n,
    tools=tools,
    tool_choice="auto",
    stream=False   # 先用非流式看完整usage
)
    cached = resp_n.usage.prompt_tokens_details.get("cached_tokens", 0)
    total_prompt = resp_n.usage.prompt_tokens
    hit_rate = cached / total_prompt if total_prompt > 0 else 0

    print(f"第{i}轮 | cached={cached} | total_prompt={total_prompt} | 命中率={hit_rate:.1%}")

    # 留出一点间隔，防止TTL过短被清
    time.sleep(1.5)

# 场景6：故意破坏缓存（最关键的对照组）
# 方法A：把第二条消息内容改一点点（模拟编辑历史）
messages_broken = messages_n.copy()
messages_broken[3]["content"] += "（偷偷加一句话）"  # 改老消息

resp_broken = client.chat.completions.create(

    model=model,
    messages=messages_broken,
    tools=tools,
    tool_choice="auto",
    stream=False   # 先用非流式看完整usage
)
print("破坏前缀后:", resp_broken.usage.prompt_tokens_details.cached_tokens)  # 预期掉到很低

# 方法B：把tools列表顺序换一下，或增删一个tool（也破坏前缀）