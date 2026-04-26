"""树形索引存储：以嵌套路径树 JSON 存储，节省 LLM token。

磁盘格式 index_tree.json（嵌套路径树）：
{
  "src": {
    "main.py": {".": "模块用途", "parse_args": "函数用途", "main": "程序入口"},
    "agent": {
      "coding_agent.py": {".": "核心 agentic 循环", "PermissionManager.check": "权限检查"}
    }
  }
}

"." 是保留 key，表示该文件节点的模块 purpose（Python 标识符不含单点，不会冲突）。
call_graph.json 格式不变。

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

# overview 渲染时折叠的"噪声"目录关键字。这些目录通常是大量自动生成 / 国际化
# 数据，对模型理解项目结构帮助极小，逐个列出反而稀释信号。命中后整个目录折叠为
# 一行 "<dir>/  (折叠 N 个模块)"。
_NOISY_DIR_KEYWORDS: frozenset[str] = frozenset({
    "locale", "locales", "migrations",
    "__pycache__", "fixtures", "vendored", "node_modules",
})


class TreeStorage:
    """树形索引存储，读写 .vibecoding/memory/ 目录下的 JSON 文件。

    额外提供 render_tree_text()，供 read_index 工具生成紧凑 LLM 输出。
    """

    def __init__(self, index_dir: Path) -> None:
        self._dir = index_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._tree_path = self._dir / "memory_tree.json"
        self._call_graph_path = self._dir / "call_graph.json"

        self._data: dict[str, ModuleData] = {}
        self._call_edges: list[CallEdge] = []

        self._load_all()

    # ── 加载 ────────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        self._data = self._load_data()
        self._call_edges = self._load_call_graph()

    def _load_data(self) -> dict[str, ModuleData]:
        """读取 memory_tree.json；若不存在则尝试从旧 memory.json 迁移。"""
        if self._tree_path.exists():
            raw = self._read_json(self._tree_path, {})
            return _tree_to_flat(raw)
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

    def _save_data(self) -> None:
        self._write_json(self._tree_path, _flat_to_tree(self._data))

    def _save_call_graph(self) -> None:
        self._write_json(
            self._call_graph_path,
            {"edges": [e.to_dict() for e in self._call_edges]},
        )

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # ── 模块 CRUD ───────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, ModuleData]:
        return dict(self._data)

    def get_module(self, module_path: str) -> Optional[ModuleData]:
        return self._data.get(module_path)

    def get_function(self, module_path: str, qualname: str) -> Optional[FunctionData]:
        module = self._data.get(module_path)
        if module is None:
            return None
        return module.functions.get(qualname)

    def upsert_module(self, module_path: str, module: ModuleData) -> None:
        with self._lock:
            self._data[module_path] = module
            self._save_data()

    def delete_module(self, module_path: str) -> None:
        with self._lock:
            if module_path not in self._data:
                return
            module_keys = {
                f"{module_path}:{q}"
                for q in self._data[module_path].functions
            }
            del self._data[module_path]
            self._call_edges = [
                e for e in self._call_edges
                if e.caller not in module_keys and e.callee not in module_keys
            ]
            self._save_data()
            self._save_call_graph()

    # ── 调用图 ──────────────────────────────────────────────────────────────

    def all_edges(self) -> list[CallEdge]:
        return list(self._call_edges)

    def set_edges_for_module(self, module_path: str, calls_map: dict[str, list[str]]) -> None:
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

    # ── 搜索 ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        top_k: int = 20,
    ) -> list[tuple[str, str, FunctionData]]:
        query_lower = query.lower()
        results: list[tuple[str, str, FunctionData]] = []
        for module_path, module in self._data.items():
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
        return _render_tree_text(self._data)

    def render_overview_text(self) -> str:
        return _render_overview_text(self._data)


# ── 扁平 ↔ 树形 互转 ────────────────────────────────────────────────────────

def _flat_to_tree(flat: dict[str, ModuleData]) -> dict:
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
                # 嵌套层：``Foo.bar`` 或 ``lazy.__proxy__.__init__``
                class_name, method_name = qualname.split(".", 1)
                existing = file_node.get(class_name)
                if isinstance(existing, str):
                    # 该名字已先以"类自身 purpose"形式存为字符串 → 升级为 dict 并
                    # 把原 purpose 暂存到 _MOD_KEY，再写入子条目。
                    file_node[class_name] = {_MOD_KEY: existing}
                elif not isinstance(existing, dict):
                    file_node[class_name] = {}
                file_node[class_name][method_name] = func.purpose
            else:
                # 顶层函数 / 类自身
                existing = file_node.get(qualname)
                if isinstance(existing, dict):
                    # 之前已写入子条目（dict）→ 把自己 purpose 存到 _MOD_KEY
                    existing[_MOD_KEY] = func.purpose
                else:
                    file_node[qualname] = func.purpose
        node[parts[-1]] = file_node
    return root


def _tree_to_flat(tree: dict, _prefix: str = "") -> dict[str, ModuleData]:
    result: dict[str, ModuleData] = {}
    for key, value in tree.items():
        path = f"{_prefix}{key}" if _prefix else key
        if not isinstance(value, dict):
            continue
        if _MOD_KEY in value:
            purpose = str(value.get(_MOD_KEY, ""))
            functions: dict[str, FunctionData] = {}
            for k, v in value.items():
                if k == _MOD_KEY:
                    continue
                if isinstance(v, str):
                    functions[k] = FunctionData(purpose=v)
                elif isinstance(v, dict):
                    # class 分组：dict 内 _MOD_KEY 是类自身 purpose（若有）
                    if _MOD_KEY in v:
                        functions[k] = FunctionData(purpose=str(v[_MOD_KEY]))
                    for method_name, method_purpose in v.items():
                        if method_name == _MOD_KEY:
                            continue
                        functions[f"{k}.{method_name}"] = FunctionData(
                            purpose=str(method_purpose)
                        )
            result[path] = ModuleData(purpose=purpose, functions=functions)
        else:
            result.update(_tree_to_flat(value, _prefix=path + "/"))
    return result


# ── 文本渲染辅助 ─────────────────────────────────────────────────────────────

def _render_tree_text(flat: dict[str, ModuleData]) -> str:
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
        node[parts[-1]] = module_data.purpose

    lines: list[str] = []
    _render_overview_node(tree, lines, 0)
    return "\n".join(lines)


def _count_modules(node: dict) -> int:
    """统计 overview 子树下叶子模块数量（值为 str 即一个模块）。"""
    n = 0
    for v in node.values():
        if isinstance(v, str):
            n += 1
        elif isinstance(v, dict):
            n += _count_modules(v)
    return n


def _render_overview_node(node: dict, lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    for key in sorted(node.keys()):
        value = node[key]
        if isinstance(value, str):
            lines.append(f"{prefix}{key}  {value}" if value else f"{prefix}{key}")
        else:
            # 噪声目录折叠：locale / migrations / __pycache__ 等
            if key in _NOISY_DIR_KEYWORDS:
                count = _count_modules(value)
                lines.append(f"{prefix}{key}/  (折叠 {count} 个模块)")
                continue
            lines.append(f"{prefix}{key}/")
            _render_overview_node(value, lines, indent + 1)


def _render_node(node: dict, lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    for key in sorted(node.keys()):
        value = node[key]
        if isinstance(value, tuple):
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
            lines.append(f"{prefix}{key}/")
            _render_node(value, lines, indent + 1)
