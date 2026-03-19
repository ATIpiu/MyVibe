# Plan 06 - 编译验证与多语言扩展

**解决 TODO**: 26, 28
**优先级**: P1
**依赖**: 现有工具系统 `src/tools/`

---

## 目标

1. 在 Agent 写入文件后自动执行语法验证（Python 优先，可扩展）
2. 扩展上下文解析器支持更多语言（JS/TS、Java 基础支持）

---

## 第一部分：pycompile 验证工具

### 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tools/compile_tool.py` | 新建 | 编译/语法验证工具 |
| `src/agent/tool_executor.py` | 修改 | 写文件后触发验证 |
| `src/agent/coding_agent.py` | 修改 | 注册验证工具 |

### 实现步骤

#### Step 1: 创建 `src/tools/compile_tool.py`

```python
import ast
import py_compile
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

def validate_python(file_path: str) -> dict:
    """
    验证 Python 文件语法
    返回: {"valid": bool, "errors": list[str], "warnings": list[str]}
    """
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "errors": [f"文件不存在: {file_path}"], "warnings": []}

    errors = []
    warnings = []

    # 阶段 1: AST 解析（语法检查）
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=file_path)
    except SyntaxError as e:
        errors.append(f"语法错误 (行 {e.lineno}): {e.msg}")
        return {"valid": False, "errors": errors, "warnings": warnings}

    # 阶段 2: py_compile（字节码编译）
    try:
        py_compile.compile(file_path, doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(f"编译错误: {e}")
        return {"valid": False, "errors": errors, "warnings": warnings}

    # 阶段 3: 可选 - 运行 pyflakes（需安装）
    try:
        result = subprocess.run(
            ["python", "-m", "pyflakes", file_path],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout:
            warnings.extend(result.stdout.strip().split("\n"))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # pyflakes 不可用时跳过

    return {"valid": True, "errors": errors, "warnings": warnings}


def validate_javascript(file_path: str) -> dict:
    """使用 node --check 验证 JS 语法（需安装 Node.js）"""
    errors = []
    try:
        result = subprocess.run(
            ["node", "--check", file_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            errors.append(result.stderr.strip())
            return {"valid": False, "errors": errors, "warnings": []}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"valid": True, "errors": [], "warnings": ["node 不可用，跳过 JS 验证"]}

    return {"valid": True, "errors": [], "warnings": []}


VALIDATORS = {
    ".py": validate_python,
    ".js": validate_javascript,
    ".ts": validate_javascript,  # ts 用 node 基础检查
}

def validate_file(file_path: str) -> str:
    """统一入口，根据扩展名选择验证器，返回格式化结果字符串"""
    ext = Path(file_path).suffix.lower()
    validator = VALIDATORS.get(ext)

    if not validator:
        return f"不支持的文件类型 {ext}，跳过验证"

    result = validator(file_path)

    if result["valid"]:
        msg = f"✅ 验证通过: {file_path}"
        if result["warnings"]:
            msg += "\n警告:\n" + "\n".join(f"  - {w}" for w in result["warnings"])
        return msg
    else:
        msg = f"❌ 验证失败: {file_path}\n错误:\n"
        msg += "\n".join(f"  - {e}" for e in result["errors"])
        return msg


# Anthropic 工具定义
VALIDATE_TOOL = {
    "name": "validate_file",
    "description": "验证文件的语法正确性。支持 Python (.py)、JavaScript (.js/.ts)。",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要验证的文件路径",
            }
        },
        "required": ["file_path"],
    },
}
```

#### Step 2: 写文件后自动触发验证

在 `src/agent/tool_executor.py` 的 `write_file` 工具处理中追加验证：

```python
elif tool_name == "write_file":
    # 现有写文件逻辑
    result = self._write_file(tool_input)

    # 写入成功后自动验证
    file_path = tool_input.get("file_path", "")
    if file_path:
        from src.tools.compile_tool import validate_file
        validation = validate_file(file_path)
        result = f"{result}\n\n{validation}"

    return result
```

---

## 第二部分：多语言解析器扩展

### 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/context/parsers/js_parser.py` | 新建 | JS/TS 符号提取 |
| `src/context/parsers/java_parser.py` | 新建 | Java 基础符号提取 |
| `src/context/parsers/parser_registry.py` | 修改 | 注册新解析器 |

#### Step 3: JavaScript 解析器 `src/context/parsers/js_parser.py`

```python
import re
from pathlib import Path

# JS/TS 符号提取 patterns
FUNCTION_PATTERNS = [
    r'function\s+(\w+)\s*\(',           # function foo(
    r'const\s+(\w+)\s*=\s*(?:async\s+)?(?:\(.*?\)|(\w+))\s*=>',  # const foo = () =>
    r'(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)',  # export function
    r'(\w+)\s*:\s*(?:async\s+)?function',  # method: function
]

CLASS_PATTERN = r'class\s+(\w+)'

def extract_symbols(file_path: str) -> list[dict]:
    """提取 JS/TS 文件中的符号"""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return []

    symbols = []

    for line_no, line in enumerate(source.split("\n"), 1):
        # 类
        for m in re.finditer(CLASS_PATTERN, line):
            symbols.append({
                "name": m.group(1),
                "type": "class",
                "line": line_no,
                "file": file_path,
            })
        # 函数
        for pattern in FUNCTION_PATTERNS:
            for m in re.finditer(pattern, line):
                name = m.group(1) or m.group(2)
                if name:
                    symbols.append({
                        "name": name,
                        "type": "function",
                        "line": line_no,
                        "file": file_path,
                    })

    return symbols
```

#### Step 4: Java 解析器 `src/context/parsers/java_parser.py`

```python
import re
from pathlib import Path

CLASS_PATTERN = r'(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)'
METHOD_PATTERN = r'(?:public|private|protected)\s+(?:static\s+)?(?:\w+)\s+(\w+)\s*\('

def extract_symbols(file_path: str) -> list[dict]:
    """提取 Java 文件中的类和方法"""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return []

    symbols = []
    for line_no, line in enumerate(source.split("\n"), 1):
        for m in re.finditer(CLASS_PATTERN, line):
            symbols.append({"name": m.group(1), "type": "class", "line": line_no, "file": file_path})
        for m in re.finditer(METHOD_PATTERN, line):
            symbols.append({"name": m.group(1), "type": "method", "line": line_no, "file": file_path})

    return symbols
```

#### Step 5: 注册到解析器注册表

```python
# src/context/parsers/parser_registry.py
from src.context.parsers import js_parser, java_parser

PARSER_MAP = {
    ".py": python_parser,
    ".js": js_parser,
    ".ts": js_parser,
    ".jsx": js_parser,
    ".tsx": js_parser,
    ".java": java_parser,
}

def get_parser(file_path: str):
    ext = Path(file_path).suffix.lower()
    return PARSER_MAP.get(ext)
```

---

## 验证方法

### 编译验证
1. 让 Agent 写入一个包含语法错误的 Python 文件
2. 确认验证结果中出现 ❌ 和错误行号
3. 修复语法错误后重新写入，确认出现 ✅
4. 写入 JS 文件后确认 node 验证触发（若 node 已安装）

### 多语言解析
1. 对 `*.js` 文件调用符号提取，确认函数名正确
2. 对 `*.java` 文件调用，确认类名和方法名提取正确
3. 在 # 补全中输入 JS 函数名前缀，确认补全出现

---

## 注意事项

- pyflakes 是可选依赖，不安装也不影响基础语法检查
- node 不可用时 JS 验证降级（跳过，不报错）
- 自动验证应在写文件工具调用后，作为额外信息附在结果中，不阻断流程
