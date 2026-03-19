"""Skill 注册表：全局单例，管理所有已加载的 Skill。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .skill_loader import Skill, load_skills_from_dir

# 用户自定义 Skill 目录
USER_SKILLS_DIR = Path.home() / ".myvibe" / "skills"
# 内置 Skill 目录（与本文件同级的 builtin/）
BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin"


class SkillRegistry:
    """技能注册表，支持内置和用户自定义技能。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_all(self) -> int:
        """加载所有技能，用户技能可覆盖同名内置技能。返回加载数量。"""
        # 先加载内置
        for skill in load_skills_from_dir(BUILTIN_SKILLS_DIR):
            self._skills[skill.name] = skill
        # 再加载用户（可覆盖内置）
        for skill in load_skills_from_dir(USER_SKILLS_DIR):
            self._skills[skill.name] = skill
        return len(self._skills)

    def register(self, skill: Skill) -> None:
        """手动注册一个 Skill（用于测试或动态创建）。"""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """按名称精确查找。"""
        return self._skills.get(name)

    def find_by_trigger(self, text: str) -> Optional[Skill]:
        """在消息文本中查找匹配 trigger 的 Skill（第一个匹配优先）。"""
        for skill in self._skills.values():
            if any(t in text for t in skill.triggers):
                return skill
        return None

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def completions(self) -> list[tuple[str, str]]:
        """返回 (name, description) 用于补全提示。"""
        return [(s.name, s.description) for s in self._skills.values()]

    def __len__(self) -> int:
        return len(self._skills)


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_registry: Optional[SkillRegistry] = None


def get_registry() -> SkillRegistry:
    """获取全局 Skill 注册表单例。"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
