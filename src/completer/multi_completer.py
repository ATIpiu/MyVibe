"""多模式补全器：根据前缀符号（@, #, /）自动选择补全器。"""
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from src.completer.file_completer import FileCompleter
from src.completer.symbol_completer import SymbolCompleter
from src.completer.command_completer import CommandCompleter


class MultiCompleter(Completer):
    """多模式补全器，根据输入前缀符号自动选择补全器。
    
    支持的模式：
    - @ 后：文件名补全
    - # 后：符号（函数/类/变量）补全
    - / 后：命令补全
    """
    
    def __init__(self, project_root: str = "."):
        """初始化多模式补全器。
        
        Args:
            project_root: 项目根目录
        """
        self.project_root = project_root
        self.file_completer = FileCompleter(project_root)
        self.symbol_completer = SymbolCompleter(project_root)
        self.command_completer = CommandCompleter()
    
    def update_root(self, project_root: str) -> None:
        """更新项目根目录。
        
        Args:
            project_root: 新的项目根目录
        """
        self.project_root = project_root
        self.file_completer.update_root(project_root)
        # 符号补全器和命令补全器不需要更新根目录
    
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """根据输入前缀选择合适的补全器。
        
        Args:
            document: 当前文档
            complete_event: 补全事件
            
        Yields:
            Completion 对象
        """
        # 获取当前行文本
        text_before_cursor = document.text_before_cursor
        
        # 检查最后一个特殊符号
        # 优先级：@ > # > /
        
        # 查找最后一个特殊符号
        last_at = text_before_cursor.rfind("@")
        last_hash = text_before_cursor.rfind("#")
        last_slash = text_before_cursor.rfind("/")
        
        # 找出最靠后的符号
        max_pos = max(last_at, last_hash, last_slash)
        
        if max_pos == -1:
            # 没有特殊符号，不补全
            return
        
        # 检查特殊符号后是否有空格（有则表示不是补全模式）
        # 需要考虑：前一个字符可能是空格，那么应该继续之前的模式
        after_symbol = text_before_cursor[max_pos + 1:]
        
        # 如果符号后紧跟空格且还有内容，说明不是补全模式
        # 例如：@  或 #  或 /  (后跟空格)
        if after_symbol.startswith(" "):
            return
        
        # 确定使用哪个补全器
        if max_pos == last_at:
            # @ 触发文件名补全
            yield from self.file_completer.get_completions(document, complete_event)
        elif max_pos == last_hash:
            # # 触发符号补全
            yield from self.symbol_completer.get_completions(document, complete_event)
        elif max_pos == last_slash:
            # / 触发命令补全
            yield from self.command_completer.get_completions(document, complete_event)
