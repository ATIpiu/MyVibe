"""树形记忆存储：以嵌套路径树 JSON 存储，节省 LLM token。

磁盘格式 memory_tree.json（嵌套路径树）：
{
  "src": {
    "main.py": {".": "模块用途", "parse_args": "函数用途", "main": "程序入口"},
    "agent": {
      "coding_agent.py": {".": "核心 agentic 循环", "PermissionManager.check": "权限检查"}
    }
  }
}

"." 是保留 key，表示该文件节点的模块 purpose（Python 标识符不含单点，不会冲突）。
call_graph.json 格式不变（与 MemoryStorage 完全兼容）。

LLM 可读的紧凑树形文本（render_tree_text()）：
  src/
    main.py  模块用途
      parse_args  函数用途
      main  程序入口
    agent/
      coding_agent.py  核心 agentic 循环
        PermissionManager.check  权限检查
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .models import CallEdge, FunctionData, ModuleData

_MOD_KEY = "."   # 文件节点中表示"模块 purpose"的保留 key


class TreeStorage:
    """树形记忆存储，公开接口与 MemoryStorage 完全兼容（可直接替换）。

    额外提供 render_tree_text()，供 read_memory 工具生成紧凑 LLM 输出。
    """

    def __init__(self, memory_dir: Path) -> None:
        self._dir = memory_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._tree_path = self._dir / "memory_tree.json"
        self._call_graph_path = self._dir / "call_graph.json"

        self._memory: dict[str, ModuleData] = {}
        self._call_edges: list[CallEdge] = []

        self._load_all()

    # ── 加载 ────────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        self._memory = self._load_memory()
        self._call_edges = self._load_call_graph()

    def _load_memory(self) -> dict[str, ModuleData]:
        """读取 memory_tree.json；若不存在则尝试从旧 memory.json 迁移。"""
        if self._tree_path.exists():
            raw = self._read_json(self._tree_path, {})
            return _tree_to_flat(raw)
        # 旧格式迁移：首次切换时自动将 memory.json 内容导入
        legacy = self._dir / "memory.json"
        raw = self._read_json(legacy, {})
        return {k: ModuleData.from_dict(v) for k, v in raw.items()}

    def _load_call_graph(self) -> list[CallEdge]:
        raw = self._read_json(self._call_graph_path, {"edges": []})
        return [CallEdge.from_dict(e) for e in raw.get("edges", [])]

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8")) or default
        except (json.JSONDecodeError, OSError):
            return default

    # ── 保存 ────────────────────────────────────────────────────────────────

    def _save_memory(self) -> None:
        self._write_json(self._tree_path, _flat_to_tree(self._memory))

    def _save_call_graph(self) -> None:
        self._write_json(
            self._call_graph_path,
            {"edges": [e.to_dict() for e in self._call_edges]},
        )

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ── 模块 CRUD（与 MemoryStorage 接口完全相同） ───────────────────────────

    def get_all(self) -> dict[str, ModuleData]:
        """返回全部记忆（只读副本）。"""
        return dict(self._memory)

    def get_module(self, module_path: str) -> Optional[ModuleData]:
        return self._memory.get(module_path)

    def get_function(self, module_path: str, qualname: str) -> Optional[FunctionData]:
        module = self._memory.get(module_path)
        if module is None:
            return None
        return module.functions.get(qualname)

    def upsert_module(self, module_path: str, module: ModuleData) -> None:
        """写入或更新整个模块（含其函数）。线程安全。"""
        with self._lock:
            self._memory[module_path] = module
            self._save_memory()

    def delete_module(self, module_path: str) -> None:
        """删除模块及其所有函数的记忆，并级联删除调用图边。"""
        with self._lock:
            if module_path not in self._memory:
                return
            module_keys = {
                f"{module_path}:{q}"
                for q in self._memory[module_path].functions
            }
            del self._memory[module_path]
            self._call_edges = [
                e for e in self._call_edges
                if e.caller not in module_keys and e.callee not in module_keys
            ]
            self._save_memory()
            self._save_call_graph()

    # ── 调用图（与 MemoryStorage 接口完全相同） ─────────────────────────────

    def all_edges(self) -> list[CallEdge]:
        return list(self._call_edges)

    def set_edges_for_module(self, module_path: str, calls_map: dict[str, list[str]]) -> None:
        """替换某个模块所有函数的出向边。calls_map = {qualname: [callee_key, ...]}"""
        with self._lock:
            module_prefix = f"{module_path}:"
            self._call_edges = [
                e for e in self._call_edges if not e.caller.startswith(module_prefix)
            ]
            for qualname, callees in calls_map.items():
                caller_key = f"{module_path}:{qualname}"
                for callee in set(callees):
                    self._call_edges.append(CallEdge(caller=caller_key, callee=callee))
            self._save_call_graph()

    def get_callers(self, function_key: str) -> list[str]:
        return [e.caller for e in self._call_edges if e.callee == function_key]

    def get_callees(self, function_key: str) -> list[str]:
        return [e.callee for e in self._call_edges if e.caller == function_key]

    # ── 搜索（与 MemoryStorage 接口完全相同） ───────────────────────────────

    def search(
        self,
        query: str = "",
        top_k: int = 20,
    ) -> list[tuple[str, str, FunctionData]]:
        """按关键词搜索函数，返回 [(module_path, qualname, FunctionData), ...]。"""
        query_lower = query.lower()
        results: list[tuple[str, str, FunctionData]] = []
        for module_path, module in self._memory.items():
            for qualname, func in module.functions.items():
                if query_lower:
                    text = f"{qualname} {func.purpose} {module_path}".lower()
                    if query_lower not in text:
                        continue
                results.append((module_path, qualname, func))
        return results[:top_k]

    def reload(self) -> None:
        with self._lock:
            self._load_all()

    # ── 树形渲染（供 LLM 读取） ──────────────────────────────────────────────

    def render_tree_text(self) -> str:
        """将全部记忆渲染为紧凑缩进树形文本，大幅节省 token。

        示例：
            src/
              main.py  CLI 入口
                parse_args  解析 CLI 参数
                main  程序入口
              agent/
                coding_agent.py  核心 agentic 循环
                  PermissionManager.check  主权限检查
        """
        return _render_tree_text(self._memory)

    def render_overview_text(self) -> str:
        """只渲染文件路径树+模块描述，不输出函数列表。"""
        return _render_overview_text(self._memory)


# ── 扁平 ↔ 树形 互转 ────────────────────────────────────────────────────────

def _flat_to_tree(flat: dict[str, ModuleData]) -> dict:
    """将 {module_path: ModuleData} 转换为嵌套路径树（磁盘存储格式）。

    类方法（qualname 含 "."）折叠到类节点：
      {"ClassName": {"method": "purpose", ...}}
    顶层函数直接存为字符串：
      {"func_name": "purpose"}
    """
    root: dict = {}
    for module_path, module_data in sorted(flat.items()):
        parts = module_path.replace("\\", "/").split("/")
        node = root
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        file_node: dict = {_MOD_KEY: module_data.purpose}
        for qualname, func in module_data.functions.items():
            if "." in qualname:
                class_name, method_name = qualname.split(".", 1)
                if class_name not in file_node:
                    file_node[class_name] = {}
                file_node[class_name][method_name] = func.purpose
            else:
                file_node[qualname] = func.purpose
        node[parts[-1]] = file_node
    return root


def _tree_to_flat(tree: dict, _prefix: str = "") -> dict[str, ModuleData]:
    """将嵌套路径树还原为 {module_path: ModuleData}（加载时反序列化）。"""
    result: dict[str, ModuleData] = {}
    for key, value in tree.items():
        path = f"{_prefix}{key}" if _prefix else key
        if not isinstance(value, dict):
            continue
        if _MOD_KEY in value:
            # 文件节点
            purpose = str(value.get(_MOD_KEY, ""))
            functions: dict[str, FunctionData] = {}
            for k, v in value.items():
                if k == _MOD_KEY:
                    continue
                if isinstance(v, str):
                    # 顶层函数
                    functions[k] = FunctionData(purpose=v)
                elif isinstance(v, dict):
                    # 类节点：展开为 ClassName.method qualname
                    for method_name, method_purpose in v.items():
                        functions[f"{k}.{method_name}"] = FunctionData(purpose=str(method_purpose))
            result[path] = ModuleData(purpose=purpose, functions=functions)
        else:
            # 目录节点，递归展开
            result.update(_tree_to_flat(value, _prefix=path + "/"))
    return result


# ── 文本渲染辅助 ─────────────────────────────────────────────────────────────

def _render_tree_text(flat: dict[str, ModuleData]) -> str:
    """渲染 memory 为紧凑缩进树形文本，类方法折叠在类名下。

    示例：
        src/
          ui/
            key_listener.py  Ctrl+O 键盘监听器
              CtrlOListener
                __init__
                start
                stop
    """
    # 渲染树节点类型：
    #   目录节点 = dict（值为更多 dict 或 file-tuple）
    #   文件节点 = tuple(purpose, top_funcs, classes)
    #     top_funcs: {qualname: purpose}       顶层函数
    #     classes:   {class_name: {method: purpose}}  类
    tree: dict = {}
    for module_path, module_data in sorted(flat.items()):
        parts = module_path.replace("\\", "/").split("/")
        node = tree
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child

        top_funcs: dict[str, str] = {}
        classes: dict[str, dict[str, str]] = {}
        for qualname, func_data in module_data.functions.items():
            if "." in qualname:
                class_name, method_name = qualname.split(".", 1)
                classes.setdefault(class_name, {})[method_name] = func_data.purpose
            else:
                top_funcs[qualname] = func_data.purpose

        node[parts[-1]] = (module_data.purpose, top_funcs, classes)

    lines: list[str] = []
    _render_node(tree, lines, indent=0)
    return "\n".join(lines)


def _render_overview_text(flat: dict[str, ModuleData]) -> str:
    tree: dict = {}
    for module_path, module_data in sorted(flat.items()):
        parts = module_path.replace("\\", "/").split("/")
        node = tree
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = module_data.purpose  # str = 叶节点

    lines: list[str] = []
    _render_overview_node(tree, lines, 0)
    return "\n".join(lines)


def _render_overview_node(node: dict, lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    for key in sorted(node.keys()):
        value = node[key]
        if isinstance(value, str):
            lines.append(f"{prefix}{key}  {value}" if value else f"{prefix}{key}")
        else:
            lines.append(f"{prefix}{key}/")
            _render_overview_node(value, lines, indent + 1)


def _render_node(node: dict, lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    for key in sorted(node.keys()):
        value = node[key]
        if isinstance(value, tuple):
            # 文件节点: (purpose, top_funcs, classes)
            purpose, top_funcs, classes = value
            lines.append(f"{prefix}{key}  {purpose}" if purpose else f"{prefix}{key}")
            item_p = "  " * (indent + 1)
            method_p = "  " * (indent + 2)
            for qualname, fpurpose in top_funcs.items():
                lines.append(f"{item_p}{qualname}  {fpurpose}" if fpurpose else f"{item_p}{qualname}")
            for class_name, methods in sorted(classes.items()):
                lines.append(f"{item_p}{class_name}")
                for method_name, mpurpose in methods.items():
                    lines.append(f"{method_p}{method_name}  {mpurpose}" if mpurpose else f"{method_p}{method_name}")
        else:
            # 目录节点
            lines.append(f"{prefix}{key}/")
            _render_node(value, lines, indent + 1)
