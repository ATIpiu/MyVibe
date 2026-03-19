# Plan 03 - 自定义 Skill 接口

**解决 TODO**: 14
**优先级**: P0（推荐优先实施）
**参考**: `learn-claude-code/s05_skill_loading.py`

---

## 目标

实现类似 Claude Code 的 `/skill-name` 调用机制。用户可在 `~/.myvibe/skills/` 目录放置 Markdown 文件，每个文件定义一个可调用的 Skill（技能），系统动态加载并集成到斜杠命令系统。

---

## Skill 文件格式

```markdown
---
name: commit
description: 生成规范的 Git commit message
triggers:
  - commit
  - 提交
args: "-m '提交信息'"
---

请根据当前 git diff 的变更内容，生成一个规范的 Git commit message。

格式要求：
- 第一行：`<type>(<scope>): <简短描述>`（不超过 72 字符）
- type 可选：feat, fix, docs, refactor, test, chore
- 如有必要，空一行后添加详细说明

请直接输出 commit message，不要有多余解释。
```

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/skills/__init__.py` | 新建 | 模块初始化 |
| `src/skills/skill_loader.py` | 新建 | Skill 加载与解析 |
| `src/skills/skill_registry.py` | 新建 | 技能注册表 |
| `src/completer/command_completer.py` | 修改 | 集成 Skill 到斜杠命令 |
| `src/main.py` | 修改 | 初始化时加载 Skills |

---

## 实现步骤

### Step 1: 创建 `src/skills/skill_loader.py`

参考 `learn-claude-code/s05_skill_loading.py` 的 YAML frontmatter 解析：

```python
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import yaml

@dataclass
class Skill:
    name: str
    description: str
    prompt_template: str
    triggers: list[str] = field(default_factory=list)
    args_hint: str = ""
    source_file: Optional[Path] = None

    def render(self, args: str = "") -> str:
        """渲染 prompt，替换参数占位符"""
        prompt = self.prompt_template
        if "{args}" in prompt:
            prompt = prompt.replace("{args}", args)
        elif args:
            prompt = f"{prompt}\n\n参数：{args}"
        return prompt


FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

def load_skill_from_file(file_path: Path) -> Optional[Skill]:
    """从 .md 文件加载 Skill"""
    content = file_path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(content)

    if not match:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
        prompt_body = match.group(2).strip()
    except yaml.YAMLError:
        return None

    if not meta or "name" not in meta:
        return None

    return Skill(
        name=meta["name"],
        description=meta.get("description", ""),
        prompt_template=prompt_body,
        triggers=meta.get("triggers", [meta["name"]]),
        args_hint=meta.get("args", ""),
        source_file=file_path,
    )


def load_skills_from_dir(skills_dir: Path) -> list[Skill]:
    """从目录加载所有 Skill"""
    if not skills_dir.exists():
        return []

    skills = []
    for md_file in skills_dir.glob("*.md"):
        skill = load_skill_from_file(md_file)
        if skill:
            skills.append(skill)

    return skills
```

### Step 2: 创建 `src/skills/skill_registry.py`

```python
from pathlib import Path
from src.skills.skill_loader import Skill, load_skills_from_dir

SKILLS_DIR = Path.home() / ".myvibe" / "skills"
BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin"

class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def load_all(self):
        """加载内置和用户自定义 Skills"""
        # 先加载内置，用户 Skill 可覆盖
        for skill in load_skills_from_dir(BUILTIN_SKILLS_DIR):
            self._skills[skill.name] = skill
        for skill in load_skills_from_dir(SKILLS_DIR):
            self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def find_by_trigger(self, text: str) -> Skill | None:
        """通过 trigger 关键词匹配 Skill"""
        for skill in self._skills.values():
            if any(t in text for t in skill.triggers):
                return skill
        return None

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def completions(self) -> list[tuple[str, str]]:
        """返回 (name, description) 列表供补全使用"""
        return [(s.name, s.description) for s in self._skills.values()]


# 全局单例
_registry = SkillRegistry()

def get_registry() -> SkillRegistry:
    return _registry
```

### Step 3: 修改 `src/completer/command_completer.py`

在斜杠命令补全中加入 Skill：

```python
from src.skills.skill_registry import get_registry

def get_completions(text: str) -> list:
    completions = [...existing_commands...]

    # 追加 Skill 补全
    registry = get_registry()
    for name, desc in registry.completions():
        completions.append(Completion(
            text=f"/{name}",
            display_meta=f"[skill] {desc}"
        ))

    return completions
```

### Step 4: 修改 `src/main.py`

在启动时初始化 Skill 注册表：

```python
from src.skills.skill_registry import get_registry

def main():
    registry = get_registry()
    registry.load_all()
    # ...其余启动逻辑
```

在消息处理中，检测斜杠命令是否对应 Skill：

```python
def handle_slash_command(cmd: str, args: str) -> str | None:
    """返回 None 表示不是 Skill 命令"""
    registry = get_registry()
    skill = registry.get(cmd.lstrip("/"))
    if skill:
        return skill.render(args)
    return None
```

### Step 5: 创建内置示例 Skill `src/skills/builtin/commit.md`

```markdown
---
name: commit
description: 生成规范的 Git commit message
triggers:
  - commit
---

请分析当前 git diff，生成规范的 commit message：
- 格式：`<type>(<scope>): <描述>`
- type：feat/fix/docs/refactor/test/chore

只输出 commit message，无需解释。
```

---

## 验证方法

1. 在 `~/.myvibe/skills/` 创建测试 Skill 文件
2. 启动 MyVibe，输入 `/` 检查补全列表包含自定义 Skill
3. 调用 `/skill-name` 确认 prompt 正确展开并发送给 Agent
4. 测试参数传递：`/skill-name some args`
5. 测试触发词匹配：发送包含 trigger 的消息

---

## 注意事项

- Skill 目录不存在时静默跳过，不报错
- YAML 解析错误时跳过该文件并打印警告
- 用户 Skill 可覆盖同名内置 Skill
