"""Skill 文件加载器：解析 YAML frontmatter + Markdown body。

Skill 文件格式：
    ---
    name: commit
    description: 生成规范的 Git commit message
    triggers:
      - commit
      - 提交
    ---
    这是 prompt 模板内容。支持 {args} 占位符。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    prompt_template: str
    triggers: list[str] = field(default_factory=list)
    source_file: Optional[Path] = None

    def render(self, args: str = "") -> str:
        """渲染 prompt，将 {args} 替换为传入参数。"""
        prompt = self.prompt_template
        if "{args}" in prompt:
            prompt = prompt.replace("{args}", args)
        elif args:
            prompt = f"{prompt}\n\n附加参数：{args}"
        return prompt.strip()


def load_skill_from_file(file_path: Path) -> Optional[Skill]:
    """从单个 .md 文件加载 Skill，失败时返回 None。"""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError:
        return None

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None

    raw_meta, prompt_body = match.group(1), match.group(2).strip()

    if _YAML_AVAILABLE:
        try:
            meta = yaml.safe_load(raw_meta) or {}
        except Exception:
            return None
    else:
        # 简易 YAML 降级解析（key: value 格式）
        meta: dict = {}
        for line in raw_meta.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()

    if not isinstance(meta, dict) or "name" not in meta:
        return None

    name = str(meta["name"]).strip()
    description = str(meta.get("description", "")).strip()
    triggers_raw = meta.get("triggers", [name])
    triggers = [str(t) for t in triggers_raw] if isinstance(triggers_raw, list) else [name]

    return Skill(
        name=name,
        description=description,
        prompt_template=prompt_body,
        triggers=triggers,
        source_file=file_path,
    )


def load_skills_from_dir(skills_dir: Path) -> list[Skill]:
    """扫描目录下所有 .md 文件，返回成功加载的 Skill 列表。"""
    if not skills_dir.exists():
        return []

    skills = []
    for md_file in sorted(skills_dir.glob("*.md")):
        skill = load_skill_from_file(md_file)
        if skill:
            skills.append(skill)
    return skills
