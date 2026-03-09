"""记忆系统健康检查命令行入口。"""
from __future__ import annotations

from pathlib import Path

from .memory_manager import MemoryManager


def run_health_check(project_root: str) -> str:
    """执行健康检查并返回统计报告字符串。"""
    manager = MemoryManager(project_root)
    all_memory = manager.read_all()
    total_modules = len(all_memory)
    total_functions = sum(len(m.functions) for m in all_memory.values())
    total_edges = len(manager._storage.all_edges())

    lines = [
        "=== 记忆系统健康报告 ===",
        f"模块数：{total_modules}",
        f"函数数：{total_functions}",
        f"调用图边数：{total_edges}",
        "",
        "模块列表：",
    ]
    for module_path, module_data in sorted(all_memory.items()):
        func_count = len(module_data.functions)
        desc = f"  {module_data.purpose}" if module_data.purpose else ""
        lines.append(f"  {module_path}（{func_count} 函数）{desc}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(run_health_check(str(Path(root).resolve())))
