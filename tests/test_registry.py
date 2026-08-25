"""通用内核 · 工具系统单测：Tool 抽象与 ToolRegistry。"""

from core.tools import Tool, ToolRegistry, tool


def _echo(x: str = "hi") -> dict:
    return {"status": "success", "result": x}


def test_tool_to_claude_schema():
    t = Tool("echo", "回声", {"type": "object", "properties": {}, "required": []}, _echo)
    s = t.to_claude()
    assert s["name"] == "echo"
    assert s["description"] == "回声"
    assert "input_schema" in s


def test_tool_run_ok():
    t = Tool("echo", "回声", {"type": "object", "properties": {}}, _echo)
    assert t.run({"x": "yo"}) == {"status": "success", "result": "yo"}


def test_tool_run_bad_args_returns_error():
    t = Tool("echo", "回声", {"type": "object", "properties": {}}, _echo)
    r = t.run({"unknown_arg": 1})
    assert r["status"] == "error"
    assert "参数错误" in r["result"]


def test_tool_run_internal_exception_is_caught():
    def boom() -> dict:
        raise RuntimeError("炸了")

    t = Tool("boom", "会抛异常", {"type": "object", "properties": {}}, boom)
    r = t.run({})
    assert r["status"] == "error"
    assert "工具执行异常" in r["result"]


def test_registry_register_and_execute():
    reg = ToolRegistry([Tool("echo", "回声", {"type": "object", "properties": {}}, _echo)])
    assert "echo" in reg.names()
    assert len(reg) == 1
    assert reg.execute("echo", {"x": "a"})["result"] == "a"


def test_registry_unknown_tool():
    reg = ToolRegistry()
    r = reg.execute("nope", {})
    assert r["status"] == "error"
    assert "未知工具" in r["result"]


def test_registry_claude_schema_lists_all():
    reg = ToolRegistry([
        Tool("a", "A", {"type": "object", "properties": {}}, _echo),
        Tool("b", "B", {"type": "object", "properties": {}}, _echo),
    ])
    schema = reg.claude_schema()
    assert {s["name"] for s in schema} == {"a", "b"}


def test_tool_decorator():
    @tool("dec", "装饰器工具")
    def fn(v: int = 1) -> dict:
        return {"status": "success", "result": v * 2}

    assert isinstance(fn, Tool)
    assert fn.run({"v": 3})["result"] == 6
