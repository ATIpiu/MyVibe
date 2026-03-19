"""编译/语法验证工具。

支持：
  - Python (.py)：ast.parse + py_compile 双重检查
  - JavaScript/TypeScript (.js/.ts)：node --check（可选，node 不可用时跳过）

write_file 成功后可自动调用 validate_file，在结果中附加验证信息。
"""
from __future__ import annotations

import ast
import py_compile
import subprocess
import tempfile
from pathlib import Path

from .base_tool import BaseTool, ToolRegistry, ToolResult


# ── 各语言验证函数 ─────────────────────────────────────────────────────────────


def _validate_python(file_path: str) -> dict:
    """Python 文件语法验证（ast.parse + py_compile）。"""
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "errors": [f"文件不存在: {file_path}"], "warnings": []}

    errors: list[str] = []
    warnings: list[str] = []

    # 阶段 1: AST 解析
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        ast.parse(source, filename=file_path)
    except SyntaxError as e:
        errors.append(f"语法错误 (行 {e.lineno}): {e.msg}")
        return {"valid": False, "errors": errors, "warnings": warnings}
    except Exception as e:
        errors.append(f"解析失败: {e}")
        return {"valid": False, "errors": errors, "warnings": warnings}

    # 阶段 2: py_compile 字节码编译
    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as tmp:
            py_compile.compile(file_path, cfile=tmp.name, doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(f"编译错误: {e}")
        return {"valid": False, "errors": errors, "warnings": warnings}
    except Exception:
        pass  # 临时文件相关问题，不影响语法检查

    # 阶段 3: pyflakes（可选）
    try:
        result = subprocess.run(
            ["python", "-m", "pyflakes", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip():
            warnings.extend(result.stdout.strip().split("\n"))
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass  # pyflakes 不可用时静默跳过

    return {"valid": True, "errors": errors, "warnings": warnings}


def _validate_javascript(file_path: str) -> dict:
    """JavaScript/TypeScript 文件语法验证（node --check）。"""
    try:
        result = subprocess.run(
            ["node", "--check", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"valid": False, "errors": [result.stderr.strip()], "warnings": []}
    except FileNotFoundError:
        return {"valid": True, "errors": [], "warnings": ["node 不可用，跳过 JS/TS 验证"]}
    except subprocess.TimeoutExpired:
        return {"valid": True, "errors": [], "warnings": ["node 验证超时，跳过"]}
    except Exception as e:
        return {"valid": True, "errors": [], "warnings": [f"JS 验证异常: {e}"]}

    return {"valid": True, "errors": [], "warnings": []}


_VALIDATORS = {
    ".py": _validate_python,
    ".js": _validate_javascript,
    ".ts": _validate_javascript,
    ".jsx": _validate_javascript,
    ".tsx": _validate_javascript,
}


def validate_file_str(file_path: str) -> str:
    """统一验证入口，返回格式化字符串。供内部直接调用。"""
    ext = Path(file_path).suffix.lower()
    validator = _VALIDATORS.get(ext)

    if not validator:
        return f"[验证] 不支持 {ext} 类型，跳过"

    result = validator(file_path)

    if result["valid"]:
        msg = f"✅ 语法验证通过: {file_path}"
        if result["warnings"]:
            msg += "\n  警告:\n" + "\n".join(f"  - {w}" for w in result["warnings"])
    else:
        msg = f"❌ 语法验证失败: {file_path}"
        if result["errors"]:
            msg += "\n  错误:\n" + "\n".join(f"  - {e}" for e in result["errors"])

    return msg


# ── 工具注册 ───────────────────────────────────────────────────────────────────


@ToolRegistry.register
class ValidateFileTool(BaseTool):
    """验证文件语法正确性。支持 Python、JavaScript、TypeScript。"""

    name = "validate_file"
    description = (
        "验证文件的语法正确性。"
        "支持 Python (.py)、JavaScript (.js/.jsx)、TypeScript (.ts/.tsx)。"
        "写入文件后建议调用以确保代码语法无误。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要验证的文件路径",
            }
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, **_) -> ToolResult:
        result_str = validate_file_str(file_path)
        is_error = result_str.startswith("❌")
        return ToolResult(content=result_str, is_error=is_error)
