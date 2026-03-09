# AI Coding Agent 优化总结

## 本次优化概览

对 AI Coding Agent 进行了 6 项系统性优化，覆盖架构重构、安全性、可观测性和智能化四个维度。

---

## 优化 1：LLM 客户端抽象重构

**问题**：`client.py` 混杂了抽象接口、ZhipuAI 实现和工厂函数，扩展新 LLM 提供商困难。

**方案**：拆分为三个文件

| 文件 | 职责 |
|------|------|
| `src/llm/base_client.py` | 抽象基类 `LLMClient` + 数据类（`StreamEvent`, `ToolCall`, `LLMResponse`, `HistoryEntry`） |
| `src/llm/zhipu_client.py` | `ZhipuLLMClient` 实现，含分档计费 |
| `src/llm/client.py` | 工厂函数 `create_client_from_config()` + 向后兼容重导出 |

**收益**：新增 LLM 提供商只需继承 `LLMClient` 并实现 `_stream_chat_impl()` 和 `count_tokens()`，无需修改其他代码。

---

## 优化 2：完整对话历史持久化

**问题**：原系统只记录结构化日志，无法完整回放一次对话的全过程（系统提示词、工具调用参数与结果等）。

**方案**：在 Agent 层引入 `ConversationRecorder`

- 每轮对话追加一条 JSON 到 `logs/conversation_{session_id}.jsonl`
- 记录内容：用户输入全文、系统提示词全文、每次 LLM 迭代的输出文本、每个工具调用的**完整参数**和**完整结果**（不截断）、token 用量与耗时

**JSONL 结构**：
```json
{
  "session_id": "...",
  "turn": 3,
  "user_input": "...",
  "system_prompt": "...",
  "llm_iterations": [
    {
      "llm_text": "...",
      "stop_reason": "tool_use",
      "tool_calls": [
        {"name": "read_file", "input": {...}, "result": "...", "is_error": false, "elapsed_ms": 23}
      ]
    }
  ],
  "final_text": "...",
  "total_input_tokens": 4231,
  "total_output_tokens": 312,
  "total_cost_usd": 0.0021,
  "total_elapsed_ms": 2800
}
```

**收益**：可完整审计每次交互；方便调试 LLM 推理链；提供数据集用于后续微调。

---

## 优化 3：记忆系统 JSON 精简

**问题**：`FunctionEntry` 存储了签名、输入输出 schema、标签、访问计数等大量字段，JSON 文件臃肿，写入和读取开销大，且很多字段价值存疑。

**方案**：激进裁剪

- `FunctionEntry`：只保留 `name`, `qualname`, `purpose`（一句话描述）, `last_modified`
- `ModuleEntry`：只保留 `purpose`, `last_modified`
- 调用关系完全移到 `call_graph.json`，不在 `FunctionEntry` 中冗余存储
- JSON 序列化去掉缩进（`ensure_ascii=False`，无 `indent`）

**Token 节省估算**：一个含 200 个函数的项目，精简后 `functions.json` 从约 150KB 降至约 20KB，节省 ~87%。

---

## 优化 4：工具结果大小限制

**问题**：大文件读取或长命令输出可能一次性向 LLM 发送数万 token，消耗上下文窗口，且 LLM 通常不需要全量数据。

**方案**：在 `ToolResult.to_api_dict()` 中统一截断

```python
MAX_CONTENT_CHARS: int = 30_000  # ≈ 7500 tokens

if original_len > self.MAX_CONTENT_CHARS:
    content = content[:self.MAX_CONTENT_CHARS] + \
        f"\n\n... [内容已截断：原始 {original_len} 字符，仅向 LLM 发送前 {self.MAX_CONTENT_CHARS} 字符。如需查看后续内容，请使用 offset 参数分页读取]"
```

**收益**：
- 防止单次工具输出撑爆上下文窗口
- 截断提示中包含 offset 使用建议，LLM 可分页继续读取
- `ConversationRecorder` 记录的是**截断前的完整内容**，不影响可观测性

---

## 优化 5：Windows Shell 输出编码修复

**问题**：`subprocess.run(encoding="utf-8")` 在 Windows 上执行 cmd 命令时，GBK 编码的输出（如中文路径、错误信息）解码失败，导致乱码或 `UnicodeDecodeError`。

**方案**：切换到字节模式 + 多编码回退

```python
def _decode_output(data: bytes) -> str:
    """依次尝试 UTF-8 / GBK / latin-1，最后 fallback 到 replace 模式。"""
    candidates = ["utf-8", "gbk", "latin-1"]
    for enc in candidates:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")

# subprocess.run 改为 text=False（字节模式）
result = subprocess.run(command, shell=True, capture_output=True, text=False, ...)
stdout = _decode_output(result.stdout)
stderr = _decode_output(result.stderr)
```

**收益**：彻底解决 Windows GBK 环境下的乱码问题，同时保持 Unix UTF-8 环境兼容。

---

## 优化 6：主动记忆注入（Proactive Memory Injection）

**问题**：LLM 在处理用户请求时，不知道项目中已有哪些相关函数，容易重复实现或调用错误的 API。

**方案**：在每轮 `run_turn()` 开始前，用用户输入搜索记忆系统，将最相关的函数注入系统提示词

```python
def _build_proactive_memory(self, user_input: str) -> str:
    results = self._memory_manager.search_functions(query=user_input, top_k=8)
    if not results:
        return ""
    lines = ["## 相关函数记忆（自动检索）", ""]
    for entry in results:
        lines.append(f"- `{entry.key}`: {entry.purpose or '（无描述）'}")
    return "\n".join(lines)
```

注入后的系统提示词片段示例：
```
## 相关函数记忆（自动检索）

- `src/utils/path.py:safe_resolve`: 拒绝路径遍历攻击，解析为绝对路径
- `src/tools/file.py:ReadFileTool.execute`: 带行号读取文件，支持分页
- `src/context/context_manager.py:ContextManager.get_file_summary`: 获取文件摘要（有缓存则复用）
```

**收益**：
- LLM 在编写新代码前，自动感知项目中已有的相关工具函数
- 减少重复造轮子，提升代码一致性
- 搜索结果基于关键词匹配（name + qualname + purpose），无额外 token 成本（在工具调用之前完成）

---

## 配套：编码规范注入

在系统提示词中强制要求：

1. 新建 `.py` 文件首行必须是一句话模块说明（docstring）
2. 每个函数体首行必须是一句话描述（docstring 第一行）

这确保了记忆系统的 `purpose` 字段始终有有意义的值，让主动记忆注入的检索质量更高，形成**正向飞轮**。

---

## 架构演进对比

| 维度 | 优化前 | 优化后 |
|------|--------|--------|
| LLM 扩展 | 修改 `client.py` | 只需新增文件，继承 `LLMClient` |
| 对话可观测性 | 结构化日志（摘要） | 完整 JSONL（每字符、每工具结果） |
| 函数记忆大小 | ~150KB / 200函数 | ~20KB / 200函数（-87%） |
| Windows 兼容性 | GBK 乱码 | UTF-8/GBK/latin-1 自动适配 |
| 上下文安全性 | 工具结果无上限 | 30,000 字符截断 + 分页提示 |
| LLM 上下文感知 | 需手动查询工具 | 每轮自动注入 top-8 相关函数 |
