# tests/instrumentors/test_context_tokens.py
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.context_tokens import (
    record_context_composition, _get_token_counter, _LenCounter,
)


class FakeCounter:
    """Deterministic counter: count = len(text)."""
    def count(self, text):
        return len(text or "")


class FakeMetrics:
    def __init__(self):
        self.skill_calls = []
        self.tool_calls = []
    def record_skill_token_usage(self, tokens, attrs):
        self.skill_calls.append({"tokens": tokens, "skill_name": attrs.get("gen_ai.skill.name", "")})
    def record_tool_token_usage(self, tokens, attrs):
        self.tool_calls.append({"tokens": tokens, "tool_name": attrs.get("gen_ai.tool.name", "")})


class FakeMsg:
    def __init__(self, role, content, metadata=None, tool_calls=None, reasoning_content=None):
        self.role = role
        self.content = content
        self.metadata = metadata or {}
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


def _run(exporter, messages, tools=None, counter=None):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    with tracer.start_as_current_span("gen_ai.chat") as span:
        record_context_composition(span, fm, messages, tools or [], "gpt-4o", counter=counter or FakeCounter())
    return exporter.spans[0], fm


def test_tool_skill_body_goes_to_skill(exporter):
    sp, fm = _run(exporter, [FakeMsg("tool", "hello skill", {"is_skill_body": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("hello skill")
    assert sp.attributes["gen_ai.context.tool_results"] == 0
    assert fm.skill_calls == [{"tokens": len("hello skill"), "skill_name": "myskill"}]


def test_system_skill_pin_goes_to_skill(exporter):
    sp, fm = _run(exporter, [FakeMsg("system", "pin text", {"active_skill_pin": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("pin text")
    assert sp.attributes["gen_ai.context.system_prompt"] == 0
    assert fm.skill_calls == [{"tokens": len("pin text"), "skill_name": "myskill"}]


def test_stubbed_skill_body_caught(exporter):
    """is_skill_body=False but original_is_skill_body=True (offloaded) → still skill."""
    sp, _ = _run(exporter, [FakeMsg("tool", "stub", {"is_skill_body": False, "original_is_skill_body": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("stub")
    assert sp.attributes["gen_ai.context.tool_results"] == 0


def test_regular_tool_result(exporter):
    sp, _ = _run(exporter, [FakeMsg("tool", "result data", {})])
    assert sp.attributes["gen_ai.context.tool_results"] == len("result data")
    assert sp.attributes["gen_ai.context.skill"] == 0


def test_system_user_assistant(exporter):
    msgs = [FakeMsg("system", "sys"), FakeMsg("user", "hi"), FakeMsg("assistant", "hello")]
    sp, _ = _run(exporter, msgs)
    assert sp.attributes["gen_ai.context.system_prompt"] == len("sys")
    assert sp.attributes["gen_ai.context.user_messages"] == len("hi")
    assert sp.attributes["gen_ai.context.assistant_messages"] == len("hello")


def test_assistant_extras_counted(exporter):
    """assistant tool_calls JSON + reasoning_content added to assistant_messages."""
    m = FakeMsg("assistant", "body", tool_calls=[{"id": "x", "function": {"name": "f"}}], reasoning_content="thinking")
    sp, _ = _run(exporter, [m])
    # assistant = body + tool_calls JSON + reasoning
    assert sp.attributes["gen_ai.context.assistant_messages"] > len("body")


def test_tool_definitions_framed(exporter):
    tools = [{"type": "function", "function": {"name": "search", "description": "d", "parameters": {"type": "object"}}}]
    sp, fm = _run(exporter, [], tools=tools)
    assert sp.attributes["gen_ai.context.tool_definitions"] > 0
    assert fm.tool_calls == [{"tokens": sp.attributes["gen_ai.context.tool_definitions"], "tool_name": "search"}]


def test_estimated_flag_set(exporter):
    sp, _ = _run(exporter, [FakeMsg("user", "hi")])
    assert sp.attributes["gen_ai.usage.estimated"] is True


def test_multimodal_text_only(exporter):
    """content is a list (image + text) → only text parts counted."""
    m = FakeMsg("user", [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "x"}}])
    sp, _ = _run(exporter, [m])
    assert sp.attributes["gen_ai.context.user_messages"] == len("hello")


def test_failsoft_counter_raises(exporter):
    class _Boom:
        def count(self, text): raise RuntimeError("boom")
    sp, _ = _run(exporter, [FakeMsg("user", "hi")], counter=_Boom())
    # must not raise; context attrs simply not set (span still valid)
    assert "gen_ai.context.user_messages" not in sp.attributes


def test_fallback_no_tiktoken(monkeypatch):
    """_get_token_counter with tiktoken import failing → _LenCounter."""
    import builtins
    real_import = builtins.__import__
    def _fake_import(name, *a, **k):
        if name == "tiktoken":
            raise ImportError("no tiktoken")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    c = _get_token_counter("gpt-4o")
    assert isinstance(c, _LenCounter)
    assert c.count("hello") == len("hello") // 4
