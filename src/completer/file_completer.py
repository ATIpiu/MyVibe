"""文件名补全器：扫描项目文件，排除.开头的文件。"""
from pathlib import Path
from typing import Iterable, List

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


class FileCompleter(Completer):
    """文件名补全器，扫描项目文件，排除.开头的文件。"""
    
    def __init__(self, project_root: str = "."):
        """初始化文件补全器。
        
        Args:
            project_root: 项目根目录，默认为当前目录
        """
        self.project_root = Path(project_root).resolve()
        self._file_cache: List[str] = []
        self._refresh_cache()
    
    def _refresh_cache(self) -> None:
        """刷新文件缓存，扫描项目文件。"""
        if not self.project_root.exists():
            self._file_cache = []
            return
        
        self._file_cache = []
        # 扫描项目文件
        for item in self.project_root.rglob("*"):
            # 排除.开头的文件和目录
            if any(part.startswith(".") for part in item.parts):
                continue
            
            # 只包含文件
            if item.is_file():
                # 计算相对路径
                try:
                    rel_path = item.relative_to(self.project_root)
                    self._file_cache.append(str(rel_path))
                except ValueError:
                    # 跨驱动器路径等问题，跳过
                    pass
        
        self._file_cache.sort()
    
    def update_root(self, project_root: str) -> None:
        """更新项目根目录并刷新缓存。
        
        Args:
            project_root: 新的项目根目录
        """
        self.project_root = Path(project_root).resolve()
        self._refresh_cache()
    
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """获取文件名补全建议。
        
        Args:
            document: 当前文档
            complete_event: 补全事件
            
        Yields:
            Completion 对象
        """
        # 获取@符号后的文本
        text_before_cursor = document.text_before_cursor
        at_pos = text_before_cursor.rfind("@")
        
        if at_pos == -1:
            # 没有@符号，不补全
            return
        
        # 获取@后的文本（包括可能的空格）
        after_at = text_before_cursor[at_pos + 1:]
        
        # 计算光标前到@符号的距离（包含@符号本身）
        # 这样可以完全替换@及其后的内容
        cursor_distance = len(text_before_cursor) - at_pos
        
        # 计算需要替换的位置（从@开始替换）
        start_position = -cursor_distance
        
        # 检查是否有空格分隔符
        if " " in after_at.strip():
            # @和光标之间有空格且还有内容，不补全
            return
        
        # 获取前缀
        prefix = after_at.strip().lower()
        
        # 遍历文件列表进行匹配
        for file_path in self._file_cache:
            file_lower = file_path.lower()
            # 支持正斜杠和反斜杠匹配
            normalized_prefix = prefix.replace("/", "\\")
            normalized_path = file_lower.replace("\\", "/")
            
            if prefix in file_lower or prefix in normalized_path or normalized_prefix in file_lower:
                # 计算显示名称（高亮匹配部分）
                display = self._highlight_match(file_path, prefix)
                
                # 补全文本需要包含 @ 符号
                yield Completion(
                    text=f"@{file_path}",
                    display=display,
                    display_meta=f"文件: {file_path}",
                    start_position=start_position
                )
    
    def _highlight_match(self, text: str, prefix: str) -> str:
        """在显示文本中高亮匹配部分。
        
        Args:
            text: 原始文本
            prefix: 匹配前缀
            
        Returns:
            高亮后的文本（使用 ANSI 颜色）
        """
        if not prefix:
            return text
        
        # 简单实现：使用大写显示匹配部分
        result = []
        i = 0
        text_lower = text.lower()
        
        while i < len(text):
            # 检查是否匹配前缀
            if text_lower[i:i+len(prefix)] == prefix:
                # 找到匹配
                result.append(text[i:i+len(prefix)])
                i += len(prefix)
            else:
                result.append(text[i])
                i += 1
        
        return "".join(result)
