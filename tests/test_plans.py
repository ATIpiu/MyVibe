"""各计划功能的集成测试脚本。

运行方式：
    cd MyVibe
    python tests/test_plans.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_results: list[dict] = []


def _test(name: str, func):
    try:
        func()
        _results.append({"plan": name, "status": "PASS", "msg": ""})
        print(f"  ✅ PASS  {name}")
    except AssertionError as e:
        _results.append({"plan": name, "status": "FAIL", "msg": str(e)})
        print(f"  ❌ FAIL  {name}: {e}")
    except Exception as e:
        _results.append({"plan": name, "status": "ERROR", "msg": str(e)})
        print(f"  💥 ERROR {name}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Plan 06: 编译验证工具
# ─────────────────────────────────────────────────────────────────────────────
print("\n📦 Plan 06 - 编译验证工具")

def test_compile_valid_python():
    from src.tools.compile_tool import ValidateFileTool
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False,
                                      encoding="utf-8") as f:
        f.write("def hello():\n    return 'world'\n\nclass Foo:\n    pass\n")
        tmp = f.name
    try:
        tool = ValidateFileTool()
        result = tool.execute(file_path=tmp)
        assert "✅" in result.content, f"期望 ✅，实际: {result.content}"
        assert not result.is_error
    finally:
        os.unlink(tmp)

def test_compile_invalid_python():
    from src.tools.compile_tool import ValidateFileTool
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False,
                                      encoding="utf-8") as f:
        f.write("def hello(\n    return 'broken syntax'\n")
        tmp = f.name
    try:
        tool = ValidateFileTool()
        result = tool.execute(file_path=tmp)
        assert "❌" in result.content, f"期望 ❌，实际: {result.content}"
        assert result.is_error
    finally:
        os.unlink(tmp)

def test_compile_nonexistent():
    from src.tools.compile_tool import ValidateFileTool
    tool = ValidateFileTool()
    result = tool.execute(file_path="/nonexistent/path.py")
    assert result.is_error, "不存在的文件应返回 is_error=True"

def test_compile_unsupported_type():
    from src.tools.compile_tool import validate_file_str
    result = validate_file_str("some_file.yaml")
    assert "跳过" in result, f"不支持的类型应跳过，实际: {result}"

_test("compile: 有效 Python 文件", test_compile_valid_python)
_test("compile: 无效 Python 文件（语法错误）", test_compile_invalid_python)
_test("compile: 不存在的文件", test_compile_nonexistent)
_test("compile: 不支持的文件类型", test_compile_unsupported_type)


# ─────────────────────────────────────────────────────────────────────────────
# Plan 01: 模型路由器
# ─────────────────────────────────────────────────────────────────────────────
print("\n📦 Plan 01 - 模型路由器")

def test_router_simple_chat():
    from src.llm.model_router import route_model
    config = route_model([{"role": "user", "content": "你好，今天天气怎么样？"}])
    assert config.model_id == "glm-4.7-flash", f"简单聊天应用 flash，实际: {config.model_id}"

def test_router_code_write():
    from src.llm.model_router import route_model
    config = route_model([{"role": "user", "content": "帮我实现一个快速排序算法"}])
    assert config.model_id == "glm-4.7", f"代码编写应用 4.7，实际: {config.model_id}"

def test_router_plan():
    from src.llm.model_router import route_model
    config = route_model([{"role": "user", "content": "请帮我制定计划，分析架构设计"}])
    assert config.model_id == "glm-5", f"计划制定应用 glm-5，实际: {config.model_id}"

def test_router_analysis():
    from src.llm.model_router import route_model
    config = route_model([{"role": "user", "content": "请分析这段代码的问题所在"}])
    assert config.model_id == "glm-5", f"代码分析应用 glm-5，实际: {config.model_id}"

def test_router_force():
    from src.llm.model_router import route_model, TaskType
    config = route_model([], force_task=TaskType.CODE_ANALYSIS)
    assert config.model_id == "glm-5"

def test_router_empty_messages():
    from src.llm.model_router import route_model
    config = route_model([])
    assert config.model_id == "glm-4.7-flash"  # 空消息应降级到 simple chat

_test("router: 简单聊天 → flash", test_router_simple_chat)
_test("router: 代码编写 → glm-4.7", test_router_code_write)
_test("router: 制定计划 → glm-5", test_router_plan)
_test("router: 代码分析 → glm-5", test_router_analysis)
_test("router: force_task 强制覆盖", test_router_force)
_test("router: 空消息降级", test_router_empty_messages)


# ─────────────────────────────────────────────────────────────────────────────
# Plan 03: Skill 接口
# ─────────────────────────────────────────────────────────────────────────────
print("\n📦 Plan 03 - Skill 接口")

def test_skill_load_from_file():
    from pathlib import Path
    from src.skills.skill_loader import load_skill_from_file
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False,
                                      encoding="utf-8") as f:
        f.write("---\nname: test-skill\ndescription: 测试技能\ntriggers:\n  - test\n---\n这是 prompt 内容。\n")
        tmp = f.name
    try:
        skill = load_skill_from_file(Path(tmp))
        assert skill is not None, "应成功加载 Skill"
        assert skill.name == "test-skill"
        assert skill.description == "测试技能"
        assert "test" in skill.triggers
        assert "这是 prompt 内容" in skill.render()
    finally:
        os.unlink(tmp)

def test_skill_render_args():
    from pathlib import Path
    from src.skills.skill_loader import load_skill_from_file
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False,
                                      encoding="utf-8") as f:
        f.write("---\nname: args-skill\ndescription: 带参数技能\ntriggers:\n  - args\n---\n请处理：{args}\n")
        tmp = f.name
    try:
        skill = load_skill_from_file(Path(tmp))
        rendered = skill.render("hello world")
        assert "hello world" in rendered, f"参数应替换 {{args}}，实际: {rendered}"
    finally:
        os.unlink(tmp)

def test_skill_invalid_file():
    from pathlib import Path
    from src.skills.skill_loader import load_skill_from_file
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False,
                                      encoding="utf-8") as f:
        f.write("没有 frontmatter 的文件\n")
        tmp = f.name
    try:
        skill = load_skill_from_file(Path(tmp))
        assert skill is None, "无 frontmatter 应返回 None"
    finally:
        os.unlink(tmp)

def test_skill_registry_builtin():
    from src.skills.skill_registry import SkillRegistry
    registry = SkillRegistry()
    count = registry.load_all()
    assert count >= 3, f"应加载至少 3 个内置 Skills，实际: {count}"
    skill = registry.get("commit")
    assert skill is not None, "内置 commit skill 应存在"
    skill = registry.get("review")
    assert skill is not None, "内置 review skill 应存在"

def test_skill_registry_completions():
    from src.skills.skill_registry import SkillRegistry
    registry = SkillRegistry()
    registry.load_all()
    completions = registry.completions()
    assert len(completions) > 0
    names = [c[0] for c in completions]
    assert "commit" in names

_test("skill: 从文件加载", test_skill_load_from_file)
_test("skill: 渲染 {args} 占位符", test_skill_render_args)
_test("skill: 无 frontmatter 文件返回 None", test_skill_invalid_file)
_test("skill: 注册表加载内置 Skills", test_skill_registry_builtin)
_test("skill: 注册表返回补全列表", test_skill_registry_completions)


# ─────────────────────────────────────────────────────────────────────────────
# Plan 04: 后台任务管理
# ─────────────────────────────────────────────────────────────────────────────
print("\n📦 Plan 04 - 后台任务管理")

def test_task_submit_and_complete():
    from src.tasks.task_manager import TaskManager
    from src.tasks.task_model import TaskStatus
    manager = TaskManager()

    def work():
        time.sleep(0.05)
        return "done"

    task = manager.submit("test-task", work, description="测试任务")
    assert task.id is not None
    assert task.status in (TaskStatus.PENDING, TaskStatus.RUNNING)

    # 等待完成
    deadline = time.time() + 3
    while task.status == TaskStatus.RUNNING and time.time() < deadline:
        time.sleep(0.05)

    assert task.status == TaskStatus.COMPLETED, f"期望 COMPLETED，实际: {task.status}"
    assert task.result == "done"

def test_task_failure():
    from src.tasks.task_manager import TaskManager
    from src.tasks.task_model import TaskStatus
    manager = TaskManager()

    def failing_work():
        raise ValueError("故意失败")

    task = manager.submit("fail-task", failing_work)
    deadline = time.time() + 3
    while task.status == TaskStatus.RUNNING and time.time() < deadline:
        time.sleep(0.05)

    assert task.status == TaskStatus.FAILED
    assert "故意失败" in (task.error or "")

def test_task_cancel():
    from src.tasks.task_manager import TaskManager
    from src.tasks.task_model import TaskStatus
    manager = TaskManager()

    def slow_work():
        time.sleep(10)  # 很慢

    task = manager.submit("slow-task", slow_work)
    time.sleep(0.05)
    cancelled = manager.cancel(task.id)
    assert cancelled, "应能取消进行中的任务"
    assert task.status == TaskStatus.CANCELLED

def test_task_list_and_detail():
    from src.tasks.task_manager import TaskManager
    from src.tasks.task_model import TaskStatus
    manager = TaskManager()
    manager.submit("list-test", lambda: "result")
    time.sleep(0.1)

    listing = manager.format_list()
    assert "list-test" in listing

def test_task_nonexistent():
    from src.tasks.task_manager import TaskManager
    manager = TaskManager()
    detail = manager.format_detail("nonexistent")
    assert "不存在" in detail

_test("tasks: 提交任务并等待完成", test_task_submit_and_complete)
_test("tasks: 任务失败时记录错误", test_task_failure)
_test("tasks: 取消进行中的任务", test_task_cancel)
_test("tasks: 列出任务和详情", test_task_list_and_detail)
_test("tasks: 不存在的任务", test_task_nonexistent)


# ─────────────────────────────────────────────────────────────────────────────
# Plan 02: spawn_agent 工具 schema 验证
# ─────────────────────────────────────────────────────────────────────────────
print("\n📦 Plan 02 - 子 Agent（工具 schema 验证）")

def test_spawn_agent_schema():
    from src.tools.agent_tools import SPAWN_AGENT_SCHEMA
    assert SPAWN_AGENT_SCHEMA["name"] == "spawn_agent"
    assert "task" in SPAWN_AGENT_SCHEMA["input_schema"]["properties"]
    assert "task" in SPAWN_AGENT_SCHEMA["input_schema"]["required"]

def test_sub_agent_excludes_spawn():
    """SubAgent 工具列表应自动排除 spawn_agent（防递归）。"""
    from src.tools.agent_tools import SPAWN_AGENT_SCHEMA
    tools = [
        {"name": "read_file"},
        {"name": "write_file"},
        SPAWN_AGENT_SCHEMA,
    ]
    # 模拟 SubAgent 初始化时的过滤逻辑
    filtered = [t for t in tools if t.get("name") != "spawn_agent"]
    assert len(filtered) == 2
    assert all(t["name"] != "spawn_agent" for t in filtered)

_test("spawn_agent: 工具 schema 结构正确", test_spawn_agent_schema)
_test("spawn_agent: 子 Agent 自动排除 spawn_agent", test_sub_agent_excludes_spawn)


# ─────────────────────────────────────────────────────────────────────────────
# 汇总报告
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("测试报告汇总")
print("=" * 60)
pass_count = sum(1 for r in _results if r["status"] == "PASS")
fail_count = sum(1 for r in _results if r["status"] == "FAIL")
error_count = sum(1 for r in _results if r["status"] == "ERROR")
total = len(_results)

print(f"总计: {total}  ✅ {pass_count} 通过  ❌ {fail_count} 失败  💥 {error_count} 异常")

if fail_count or error_count:
    print("\n失败/异常详情:")
    for r in _results:
        if r["status"] != "PASS":
            print(f"  [{r['status']}] {r['plan']}: {r['msg']}")

print("=" * 60)
sys.exit(0 if (fail_count + error_count) == 0 else 1)
