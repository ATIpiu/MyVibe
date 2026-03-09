"""路径安全工具：解析、验证、规范化文件路径，防止路径遍历攻击。"""
from pathlib import Path
from typing import Optional


def safe_resolve(path: str, base_dir: Optional[str] = None) -> Path:
    """解析绝对路径，拒绝路径遍历攻击。

    Args:
        path: 要解析的路径字符串
        base_dir: 安全基目录，路径必须在此目录内

    Returns:
        解析后的绝对 Path 对象

    Raises:
        PermissionError: 路径尝试逃出 base_dir
        FileNotFoundError: 路径格式无效
    """
    resolved = Path(normalize_path(path)).resolve()
    if base_dir is not None:
        base = Path(base_dir).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            raise PermissionError(
                f"路径遍历被拒绝: '{path}' 试图逃出基目录 '{base_dir}'"
            )
    return resolved


def is_safe_path(path: str, base_dir: Optional[str] = None) -> bool:
    """safe_resolve 的 bool 版本，不抛异常。

    Args:
        path: 要检查的路径
        base_dir: 安全基目录

    Returns:
        True 表示路径安全，False 表示路径不安全或无效
    """
    try:
        safe_resolve(path, base_dir)
        return True
    except (PermissionError, FileNotFoundError, OSError):
        return False


def normalize_path(path: str) -> str:
    """展开 ~，规范化路径分隔符。

    Args:
        path: 要规范化的路径字符串

    Returns:
        规范化后的路径字符串
    """
    expanded = str(Path(path).expanduser())
    # 统一使用正斜杠（跨平台兼容）
    return expanded.replace("\\", "/")


def get_project_root(start_path: str = ".") -> Path:
    """向上遍历目录找 .git 根目录，未找到则返回 cwd。

    Args:
        start_path: 起始搜索目录

    Returns:
        项目根目录的 Path 对象
    """
    current = Path(start_path).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()
