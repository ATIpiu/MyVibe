# Plan 05 - MCP 接口与 @ # 补全增强

**解决 TODO**: 2, 16
**优先级**: P2
**依赖**: 基础补全系统已存在

---

## 目标

1. 在补全器中实现 `@文件名` 触发文件补全、`#符号名` 触发代码符号补全
2. 实现标准 MCP（Model Context Protocol）服务器协议，支持外部工具动态注册

---

## 第一部分：@ 和 # 补全增强

### 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/completer/multi_completer.py` | 修改 | 添加 @ # 触发路由 |
| `src/completer/file_completer.py` | 修改 | 支持 @ 前缀触发 |
| `src/completer/symbol_completer.py` | 新建 | # 触发的符号补全 |

### 实现步骤

#### Step 1: 修改 `src/completer/multi_completer.py`

```python
def get_completions(self, document, complete_event):
    text = document.text_before_cursor

    # @ 触发文件补全
    at_match = re.search(r'@(\S*)$', text)
    if at_match:
        prefix = at_match.group(1)
        yield from self.file_completer.get_completions_for_prefix(prefix)
        return

    # # 触发符号补全
    hash_match = re.search(r'#(\S*)$', text)
    if hash_match:
        prefix = hash_match.group(1)
        yield from self.symbol_completer.get_completions_for_prefix(prefix)
        return

    # / 触发斜杠命令补全
    if text.lstrip().startswith('/'):
        yield from self.command_completer.get_completions(document, complete_event)
        return

    # 默认：历史词补全
    yield from self.word_completer.get_completions(document, complete_event)
```

#### Step 2: 修改文件补全支持 @ 前缀

```python
# src/completer/file_completer.py
def get_completions_for_prefix(self, prefix: str):
    """为 @prefix 提供文件路径补全"""
    base_dir = Path.cwd()
    search = prefix or ""

    for path in base_dir.rglob("*"):
        if path.is_file() and search.lower() in path.name.lower():
            rel = str(path.relative_to(base_dir))
            yield Completion(
                text=rel,
                start_position=-len(search),
                display=path.name,
                display_meta=str(path.parent)
            )
```

#### Step 3: 创建符号补全 `src/completer/symbol_completer.py`

```python
import ast
from pathlib import Path
from prompt_toolkit.completion import Completer, Completion

class SymbolCompleter(Completer):
    """# 触发的代码符号补全（函数、类、变量）"""

    def _scan_python_symbols(self, file_path: Path) -> list[tuple[str, str]]:
        """扫描 Python 文件中的顶层符号"""
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            return []

        symbols = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                symbols.append((node.name, f"函数 in {file_path.name}"))
            elif isinstance(node, ast.ClassDef):
                symbols.append((node.name, f"类 in {file_path.name}"))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.append((target.id, f"变量 in {file_path.name}"))
        return symbols

    def get_completions_for_prefix(self, prefix: str):
        """为 #prefix 提供符号补全"""
        seen = set()
        for py_file in Path.cwd().rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file):
                continue
            for name, meta in self._scan_python_symbols(py_file):
                if prefix.lower() in name.lower() and name not in seen:
                    seen.add(name)
                    yield Completion(
                        text=name,
                        start_position=-len(prefix),
                        display=name,
                        display_meta=meta
                    )
```

---

## 第二部分：MCP 接口

### 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/mcp/__init__.py` | 新建 | 模块初始化 |
| `src/mcp/mcp_server.py` | 新建 | MCP 服务器实现 |
| `src/mcp/mcp_client.py` | 新建 | MCP 客户端（连接外部服务器） |
| `src/mcp/tool_adapter.py` | 新建 | MCP 工具转换为内部工具格式 |
| `src/agent/coding_agent.py` | 修改 | 注册 MCP 工具 |

### MCP 工具注册流程

```
外部 MCP 服务器 → MCP Client 发现工具 → tool_adapter 转换格式 → coding_agent 注册工具
```

### Step 4: 创建 MCP 客户端 `src/mcp/mcp_client.py`

```python
import json
import subprocess
from typing import Optional

class MCPClient:
    """连接外部 MCP 服务器，发现并调用其工具"""

    def __init__(self, server_command: list[str]):
        self.server_command = server_command
        self._process: Optional[subprocess.Popen] = None
        self._tools: list[dict] = []

    def start(self):
        """启动 MCP 服务器进程"""
        self._process = subprocess.Popen(
            self.server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._initialize()

    def _initialize(self):
        """发送 initialize 请求，获取工具列表"""
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"}})
        response = self._recv()

        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_response = self._recv()
        self._tools = tools_response.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用 MCP 工具"""
        self._send({
            "jsonrpc": "2.0", "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        })
        response = self._recv()
        content = response.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    def get_tools(self) -> list[dict]:
        return self._tools

    def _send(self, data: dict):
        line = json.dumps(data) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def _recv(self) -> dict:
        line = self._process.stdout.readline()
        return json.loads(line)

    def stop(self):
        if self._process:
            self._process.terminate()
```

### Step 5: MCP 工具适配器 `src/mcp/tool_adapter.py`

```python
def mcp_tool_to_anthropic(mcp_tool: dict) -> dict:
    """将 MCP 工具格式转换为 Anthropic API 工具格式"""
    return {
        "name": mcp_tool["name"],
        "description": mcp_tool.get("description", ""),
        "input_schema": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
    }
```

### Step 6: 配置文件支持 `~/.myvibe/mcp_servers.json`

```json
{
  "servers": [
    {
      "name": "filesystem",
      "command": ["npx", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    {
      "name": "my-custom-server",
      "command": ["python", "/path/to/my_mcp_server.py"]
    }
  ]
}
```

---

## 验证方法

### @ # 补全验证
1. 输入 `@src` 检查文件路径补全出现
2. 输入 `#main` 检查函数名补全出现
3. 按 Tab 确认补全文字正确插入

### MCP 验证
1. 配置一个标准 MCP 服务器（如 filesystem server）
2. 启动 MyVibe，确认 MCP 工具出现在工具列表
3. 调用 MCP 工具，确认结果正确返回
4. 服务器断开时的错误处理

---

## 注意事项

- 符号扫描在大型项目中可能较慢，考虑异步或增量扫描
- MCP 客户端需处理进程意外退出的情况
- MCP 配置文件不存在时不启动 MCP，不影响正常使用
