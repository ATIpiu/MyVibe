# MyVibe 开发 TODO List

> 基于 Claude Code 源码架构对比分析，按优先级排列

---

## P0｜高价值 × 低实现成本（优先做）

- [ ] **Prompt Cache 静态/动态分区**
  - system prompt 拆成静态段（规则/能力描述）+ 动态段（时间戳/git状态/记忆）
  - 静态段加 `cache_control: {"type": "ephemeral"}` 标注
  - tools 列表也加缓存标注（工具定义基本不变）
  - 预期收益：重复 token 费用降低 60-90%
  - 文件：`src/llm/prompts.py`, `src/llm/openai_client.py`, `src/llm/client.py`

- [ ] **工具并发：读写分离**
  - 给 `BaseTool` 加 `is_read_only: bool = False` 声明
  - 只读工具（read_file/grep/glob/git_status等）并发执行
  - 写操作（write_file/edit_file/shell/git_commit）互斥排队
  - 预期收益：多工具调用速度提升 2-5x
  - 文件：`src/tools/base_tool.py`, `src/agent/coding_agent.py`

- [ ] **工具安全声明（is_read_only / is_concurrency_safe）**
  - fail-closed 原则：默认 False，子类显式声明 True
  - 与权限系统联动（只读工具自动 auto_allow）
  - 文件：`src/tools/base_tool.py` + 所有工具子类

---

## P1｜高价值 × 中等成本（本周）

- [ ] **Web Search 工具**
  - 集成 Tavily API（推荐，AI Agent 友好，有免费额度）或 DuckDuckGo HTML 解析
  - 新建 `src/tools/web_search.py`
  - 解决"查不到最新 API"、"不知道报错原因"等高频失败

- [ ] **五级上下文压缩漏斗**
  - 当前：只有第4层（阈值触发 LLM 摘要）
  - 新增：
    - Snip：tool result 超长时截断保留结构
    - Microcompact：大结果卸载到缓存文件
    - Reactive Compact：API 返回 413/context_too_long 时触发
    - 断路器：连续失败 3 次停止压缩
  - 文件：`src/agent/coding_agent.py`, `src/agent/state.py`

- [ ] **Hooks 系统**
  - pre/post tool hook，工具执行前后触发 shell 命令
  - 用途：edit_file 后自动 lint/format，shell 后自动审计
  - 新建 `src/hooks/hook_manager.py`
  - 在 `handle_tool_calls` 插入调用点

---

## P2｜中等价值（下周）

- [ ] **COORDINATOR_MODE：多 Agent 协调**
  - 调研 → 合成 → 实施 → 验证 四阶段并行
  - `CoordinatorAgent` 拆解任务 → 分发给多个 SubAgent → 汇总
  - 现状：spawn_agent 只是单次隔离，无协调者

- [ ] **三层记忆架构增强**
  - 当前：AGENT.md 全量注入
  - 新增：话题文件（温数据），按用户输入相关性加载部分 AGENT.md
  - 参考：Claude Code 的 MEMORY.md 200行热数据 + 话题文件

- [ ] **Git 环境探测注入**
  - 把 git branch、最新 3 条 commit、未提交 diff 注入 system prompt 动态区
  - 文件：`src/llm/prompts.py` 的 `build_system_prompt`

- [ ] **MCP（Model Context Protocol）支持**
  - 接入第三方工具生态（数据库、Figma、Notion、GitHub 等）
  - 先做 stdio MCP client

---

## P3｜锦上添花（有空做）

- [ ] TypeScript/JavaScript 原生 AST 支持（现在是正则 generic parser）
- [ ] Browser Tool（Playwright 浏览器操作）
- [ ] 启动性能优化（TCP 预连接、延迟加载模块）
- [ ] Feature Flags 系统（config.yaml 的 `features:` 字段）

---

## 已完成

- [x] 删除旧 read_file（原始行读取），rename read_index → read_file
- [x] 清理 rename 遗留 dead code（dedup 机制重新设计）
- [x] Agent "分析完但不执行"问题（system prompt 执行语气强化）
- [x] Docker git safe.directory 自动修复
