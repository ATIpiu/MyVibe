"""日志格式常量：日志 Schema、颜色样式、事件类型。"""

# 完整日志字段 schema（每条 JSONL 行的结构）
LOG_SCHEMA = {
    "timestamp":  str,    # ISO8601，如 "2026-03-05T10:23:45.123Z"
    "session_id": str,    # 会话唯一 ID
    "turn":       int,    # 第几轮对话（从1开始）
    "level":      str,    # DEBUG / INFO / WARNING / ERROR
    "event":      str,    # 见 LogEvent 常量
    "data":       dict,   # 事件载荷（结构随 event 不同）
    "elapsed_ms": int,    # 距上条日志的毫秒数（性能分析用）
}

# Rich 控制台颜色映射
LEVEL_STYLES = {
    "DEBUG":       "dim white",
    "INFO":        "bold green",
    "WARNING":     "bold yellow",
    "ERROR":       "bold red",
    "TOOL_CALL":   "bold cyan",
    "TOOL_RESULT": "cyan",
    "LLM_REQ":     "bold magenta",
    "LLM_STREAM":  "magenta",
    "LLM_RESP":    "bold magenta",
    "PERMISSION":  "bold yellow",
    "COMPRESS":    "bold blue",
    "SESSION":     "dim green",
}


class LogEvent:
    # ── 工具调用全流程 ──────────────────────────
    TOOL_CALL        = "tool_call"        # 工具即将执行
    TOOL_RESULT      = "tool_result"      # 工具执行完毕（含耗时）
    TOOL_ERROR       = "tool_error"       # 工具抛出异常

    # ── LLM 交互全流程 ─────────────────────────
    LLM_REQUEST      = "llm_request"      # 即将发起 API 调用（含 token 估算）
    LLM_STREAM_START = "llm_stream_start" # 收到第一个 SSE 事件
    LLM_STREAM_TOKEN = "llm_stream_token" # 每个文本 delta（可选，debug 级）
    LLM_STREAM_TOOL  = "llm_stream_tool"  # 流式工具调用开始
    LLM_RESPONSE     = "llm_response"     # 完整响应聚合完毕（stop_reason + usage）

    # ── 会话 / 上下文 ───────────────────────────
    SESSION_START    = "session_start"
    SESSION_RESUME   = "session_resume"
    SESSION_SAVE     = "session_save"
    CONTEXT_COMPRESS = "context_compress" # 上下文压缩
    CONTEXT_RATIO    = "context_ratio"    # 当前 token 占比

    # ── 其他 ────────────────────────────────────
    PERMISSION_CHECK = "permission_check"
    USER_INPUT       = "user_input"
    AGENT_TURN_START = "agent_turn_start"
    AGENT_TURN_END   = "agent_turn_end"
    SUMMARY_CACHE    = "summary_cache"    # 上下文管理缓存命中/失效
