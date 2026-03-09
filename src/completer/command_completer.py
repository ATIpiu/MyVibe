"""命令补全器：提供斜杠命令补全及说明。"""
from typing import Iterable, List

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


class CommandInfo:
    """命令信息类。"""
    
    def __init__(self, name: str, description: str, usage: str = ""):
        """初始化命令信息。
        
        Args:
            name: 命令名称
            description: 命令描述
            usage: 使用说明
        """
        self.name = name
        self.description = description
        self.usage = usage
    
    @property
    def full_description(self) -> str:
        """获取完整描述。"""
        if self.usage:
            return f"{self.description} | {self.usage}"
        return self.description


class CommandCompleter(Completer):
    """命令补全器，提供斜杠命令补全及说明。"""
    
    def __init__(self):
        """初始化命令补全器。"""
        self.commands: List[CommandInfo] = [
            CommandInfo(
                name="/init",
                description="扫描项目并用 LLM 生成 MyVibe.md 项目记忆",
                usage="/init"
            ),
            CommandInfo(
                name="/context",
                description="查看上下文用量、系统提示词、MyVibe.md 及记忆统计",
                usage="/context"
            ),
            CommandInfo(
                name="/clear",
                description="清除当前对话历史",
                usage="/clear"
            ),
            CommandInfo(
                name="/compact",
                description="压缩对话历史（节省 tokens）",
                usage="/compact"
            ),
            CommandInfo(
                name="/cost",
                description="显示 token 使用量和费用",
                usage="/cost"
            ),
            CommandInfo(
                name="/sessions",
                description="列出所有历史会话",
                usage="/sessions"
            ),
            CommandInfo(
                name="/history",
                description="查看对话轮次 git 提交历史",
                usage="/history"
            ),
            CommandInfo(
                name="/revert",
                description="列出历史轮次并交互选择回退目标",
                usage="/revert"
            ),
            CommandInfo(
                name="/help",
                description="显示帮助信息",
                usage="/help"
            ),
            CommandInfo(
                name="/exit",
                description="退出程序",
                usage="/exit 或 /quit"
            ),
            CommandInfo(
                name="/quit",
                description="退出程序（同 /exit）",
                usage="/quit 或 /exit"
            ),
        ]
    
    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """获取命令补全建议。
        
        Args:
            document: 当前文档
            complete_event: 补全事件
            
        Yields:
            Completion 对象
        """
        # 获取光标前的所有文本
        text_before_cursor = document.text_before_cursor
        
        # 查找最后一个斜杠位置
        slash_pos = text_before_cursor.rfind("/")
        
        if slash_pos == -1:
            # 没有斜杠，不补全
            return
        
        # 获取斜杠后的文本
        after_slash = text_before_cursor[slash_pos + 1:]

        # start_position 只替换斜杠后的内容（不含斜杠本身）
        # 避免补全文本带斜杠时变成 //command
        start_position = -len(after_slash)

        prefix = after_slash.strip()

        # 斜杠后有空格，不补全
        if after_slash.startswith(" "):
            return

        # 遍历命令列表进行匹配
        for cmd_info in self.commands:
            cmd_name = cmd_info.name[1:]  # 去掉斜杠，只补全斜杠后的部分
            cmd_lower = cmd_name.lower()
            prefix_lower = prefix.lower()

            # 精确前缀匹配
            if cmd_lower.startswith(prefix_lower):
                yield Completion(
                    text=cmd_name,          # 不含斜杠，避免重复
                    display=cmd_info.name,  # 显示时带斜杠
                    display_meta=cmd_info.description,
                    start_position=start_position,
                )
            # 模糊匹配（至少输入 2 个字符）
            elif len(prefix) >= 2 and prefix_lower in cmd_lower:
                yield Completion(
                    text=cmd_name,
                    display=cmd_info.name,
                    display_meta=cmd_info.description,
                    start_position=start_position,
                )
