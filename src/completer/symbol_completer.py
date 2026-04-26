"""符号补全器：从项目记忆中提取函数、类、变量等符号。"""
import re
from typing import Dict, Iterable, List

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

try:
    from src.tools.index.manager import get_index_manager
    MEMORY_AVAILABLE = True
except ImportError:
    MEMORY_AVAILABLE = False


class SymbolInfo:
    """符号信息类。"""
    
    def __init__(self, name: str, symbol_type: str, module_path: str = "", description: str = ""):
        """初始化符号信息。
        
        Args:
            name: 符号名称
            symbol_type: 符号类型 (function, class, method, variable)
            module_path: 模块路径
            description: 描述
        """
        self.name = name
        self.symbol_type = symbol_type
        self.module_path = module_path
        self.description = description
    
    def to_dict(self) -> dict:
        """转换为字典。"""
        return {
            "name": self.name,
            "type": self.symbol_type,
            "module": self.module_path,
            "description": self.description
        }


class SymbolCompleter(Completer):
    """符号补全器，从项目记忆中提取函数、类等符号。"""
    
    def __init__(self, project_root: str = "."):
        """初始化符号补全器。
        
        Args:
            project_root: 项目根目录
        """
        self.project_root = project_root
        self._symbol_cache: Dict[str, List[SymbolInfo]] = {}
        self._all_symbols: List[SymbolInfo] = []
        
        if MEMORY_AVAILABLE:
            self._load_from_memory()
        else:
            # 备用方案：扫描文件使用正则
            self._load_from_scan()
    
    def _load_from_memory(self) -> None:
        """从项目记忆加载符号。"""
        try:
            index_manager = get_index_manager(self.project_root)
            all_memory = index_manager.read_all()
            
            for module_path, module_data in all_memory.items():
                # 提取函数符号
                for func_name, func_data in module_data.functions.items():
                    # 解析函数类型
                    symbol_type = "method" if "." in func_name else "function"
                    
                    # 提取第一行描述
                    description = func_data.purpose or ""
                    
                    symbol = SymbolInfo(
                        name=func_name,
                        symbol_type=symbol_type,
                        module_path=module_path,
                        description=description
                    )
                    
                    self._add_symbol(symbol)
                    
                    # 同时添加短名称（不含类前缀）
                    if "." in func_name:
                        short_name = func_name.split(".")[-1]
                        if short_name not in self._symbol_cache:
                            short_symbol = SymbolInfo(
                                name=short_name,
                                symbol_type=symbol_type,
                                module_path=module_path,
                                description=description
                            )
                            self._add_symbol(short_symbol)
                            
        except Exception as e:
            # 记忆加载失败，使用备用方案
            print(f"[警告] 记忆加载失败: {e}，使用文件扫描")
            self._load_from_scan()
    
    def _load_from_scan(self) -> None:
        """从文件扫描加载符号（备用方案）。"""
        from pathlib import Path
        
        project_path = Path(self.project_root)
        
        # Python 符号正则
        func_pattern = re.compile(r'def\s+(\w+)\s*\(')
        class_pattern = re.compile(r'class\s+(\w+)')
        method_pattern = re.compile(r'def\s+(\w+)\s*\([^)]*self[^)]*\)')
        
        # 扫描 Python 文件
        for py_file in project_path.rglob("*.py"):
            # 排除.开头的目录
            if any(part.startswith(".") for part in py_file.parts):
                continue
            
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                
                # 匹配类
                for match in class_pattern.finditer(content):
                    class_name = match.group(1)
                    self._add_symbol(SymbolInfo(
                        name=class_name,
                        symbol_type="class",
                        module_path=str(py_file),
                        description="类定义"
                    ))
                
                # 匹配函数
                for match in func_pattern.finditer(content):
                    func_name = match.group(1)
                    self._add_symbol(SymbolInfo(
                        name=func_name,
                        symbol_type="function",
                        module_path=str(py_file),
                        description="函数定义"
                    ))
                
            except Exception:
                continue
    
    def _add_symbol(self, symbol: SymbolInfo) -> None:
        """添加符号到缓存。
        
        Args:
            symbol: 符号信息
        """
        # 按首字母分组
        first_char = symbol.name[0].lower() if symbol.name else "_"
        if first_char not in self._symbol_cache:
            self._symbol_cache[first_char] = []
        self._symbol_cache[first_char].append(symbol)
        
        # 添加到全局列表
        self._all_symbols.append(symbol)
    
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """获取符号补全建议。
        
        Args:
            document: 当前文档
            complete_event: 补全事件
            
        Yields:
            Completion 对象
        """
        # 获取#符号后的文本
        text_before_cursor = document.text_before_cursor
        hash_pos = text_before_cursor.rfind("#")
        
        if hash_pos == -1:
            # 没有#符号，不补全
            return
        
        # 获取#后的文本（包括可能的空格）
        after_hash = text_before_cursor[hash_pos + 1:]
        
        # 计算光标前到#符号的距离（包含#符号本身）
        # 这样可以完全替换#及其后的内容
        cursor_distance = len(text_before_cursor) - hash_pos
        
        # 计算需要替换的位置（从#开始替换）
        start_position = -cursor_distance
        
        # 检查是否有空格分隔符
        if " " in after_hash.strip():
            return
        
        # 获取前缀
        prefix = after_hash.strip().lower()
        
        # 类型图标
        type_icons = {
            "function": "ƒ ",
            "method": "ƒ ",
            "class": "📦",
            "variable": "•",
        }
        
        # 遍历所有符号进行匹配
        for symbol in self._all_symbols:
            symbol_lower = symbol.name.lower()
            
            # 模糊匹配
            if prefix in symbol_lower:
                # 模块路径显示
                module_display = symbol.module_path
                if len(module_display) > 40:
                    module_display = "..." + module_display[-37:]
                
                # 显示文本
                display_text = f"{type_icons.get(symbol.symbol_type, '•')} {symbol.name}"
                
                # 补全文本需要包含 # 符号
                yield Completion(
                    text=f"#{symbol.name}",
                    display=display_text,
                    display_meta=f"{symbol.symbol_type} | {module_display}",
                    start_position=start_position
                )
