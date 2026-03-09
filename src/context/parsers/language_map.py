"""语言映射：文件扩展名 → 解析器类型，以及各语言函数定义正则。"""

# 扩展名到解析器的映射
LANGUAGE_MAP: dict[str, str] = {
    ".py":   "python",
    ".java": "generic",
    ".ts":   "generic",
    ".tsx":  "generic",
    ".js":   "generic",
    ".jsx":  "generic",
    ".go":   "generic",
    ".rs":   "generic",
    ".cpp":  "generic",
    ".cc":   "generic",
    ".cxx":  "generic",
    ".c":    "generic",
    ".h":    "generic",
    ".hpp":  "generic",
    ".rb":   "generic",
    ".php":  "generic",
    ".swift": "generic",
    ".kt":   "generic",
    ".cs":   "generic",
    ".scala": "generic",
}

# 各语言函数定义正则（generic_parser 使用，捕获组1为函数名）
FUNCTION_PATTERNS: dict[str, str] = {
    "java": (
        r"(?:(?:public|private|protected|static|final|native|synchronized|abstract|transient)\s+)*"
        r"[\w<>\[\]]+\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{"
    ),
    "typescript": (
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[<(]"
        r"|(?:export\s+)?(?:const|let)\s+(\w+)\s*[=:]\s*(?:async\s+)?\("
    ),
    "javascript": (
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("
        r"|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\()"
    ),
    "go":   r"func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s+)?(\w+)\s*\(",
    "rust": r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]",
    "cpp":  r"(?:[\w:*&<>]+\s+)+(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{",
    "c":    r"(?:[\w*]+\s+)+(\w+)\s*\([^;]*\)\s*\{",
    "ruby": r"def\s+(\w+[?!]?)\s*(?:\(|$)",
    "php":  r"(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(",
    "swift": r"func\s+(\w+)\s*[<(]",
    "kotlin": r"(?:fun\s+)(?:\w+\s+)?(\w+)\s*\(",
    "csharp": (
        r"(?:public|private|protected|internal|static|virtual|override|abstract|sealed|\s)+"
        r"[\w<>\[\]?]+\s+(\w+)\s*\("
    ),
    "scala": r"def\s+(\w+)\s*[:(]",
    "generic": r"(?:def|func|function|fn|sub|void|public|private)\s+(\w+)\s*[(<]",
}

# 语言别名映射（用于 FUNCTION_PATTERNS 查找）
LANGUAGE_ALIASES: dict[str, str] = {
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".cpp":  "cpp",
    ".cc":   "cpp",
    ".cxx":  "cpp",
    ".h":    "c",
    ".hpp":  "cpp",
    ".cs":   "csharp",
    ".kt":   "kotlin",
    ".rb":   "ruby",
}


def get_language(file_ext: str) -> str:
    """根据文件扩展名返回语言标识符。"""
    return LANGUAGE_MAP.get(file_ext.lower(), "generic")


def get_function_pattern(file_ext: str) -> str:
    """根据文件扩展名返回对应的函数定义正则。"""
    lang = LANGUAGE_ALIASES.get(file_ext.lower()) or get_language(file_ext)
    return FUNCTION_PATTERNS.get(lang, FUNCTION_PATTERNS["generic"])
