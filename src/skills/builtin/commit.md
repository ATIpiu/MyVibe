---
name: commit
description: 生成规范的 Git commit message
triggers:
  - commit
  - 提交信息
---
请根据当前 git diff 的变更内容，生成一个规范的 Git commit message。

格式要求：
- 第一行：`<type>(<scope>): <简短描述>`（不超过 72 字符）
- type 可选：feat / fix / docs / refactor / test / chore
- 如有必要，空一行后添加详细说明

请先调用 git_diff 查看变更，然后直接输出 commit message，不要有多余解释。
