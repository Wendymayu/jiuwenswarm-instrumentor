# tests/instrumentors/test_subagent.py
from unittest.mock import Mock
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from jiuwenswarm_instrumentor.instrumentors.subagent import instrument_subagent


class _Card:
    id = "sub-1"; name = "research_agent"


class _FakeDeepAgent:
    """Simulates DeepAgent: create_subagent returns a sub-agent instance
    whose invoke is already metaclass-wrapped (just a plain async method here)."""
    def create_subagent(self, subagent_type, subsession_id):
        class _SubAgent:
            card = _Card()
            async def invoke(self, inputs, session=None, **kw):
                return {"result_type": "success", "output": "done"}
        return _SubAgent()


async def test_subagent_invoke_span(exporter):
    tracer = trace.get_tracer("t")
    metrics = Mock()
    instrument_subagent(tracer, metrics, deep_agent_cls=_FakeDeepAgent)
    parent = _FakeDeepAgent()
    sub = parent.create_subagent("research", "sub-sess-1")
    await sub.invoke({"query": "find X"})
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.subagent.invoke"]
    assert len(spans) == 1
    sp = spans[0]
    assert sp.attributes["gen_ai.agent.name"] == "research_agent"
    assert sp.attributes["gen_ai.conversation.id"] == "sub-sess-1"
    assert sp.status.status_code == StatusCode.OK


async def test_subagent_falls_back_to_type_when_card_name_missing(exporter):
    """No card.name → span agent_name falls back to subagent_type."""
    tracer = trace.get_tracer("t")
    metrics = Mock()

    class _NoNameCard:
        id = "sub-2"; name = ""

    class _FakeNoName(_FakeDeepAgent):
        def create_subagent(self, subagent_type, subsession_id):
            class _SubAgent:
                card = _NoNameCard()
                async def invoke(self, inputs, session=None, **kw):
                    return {"result_type": "success"}
            return _SubAgent()

    instrument_subagent(tracer, metrics, deep_agent_cls=_FakeNoName)
    parent = _FakeNoName()
    sub = parent.create_subagent("general", "sub-sess-5")
    await sub.invoke({"query": "hi"})
    sp = [s for s in exporter.spans if s.name == "jiuwenclaw.subagent.invoke"][0]
    assert sp.attributes["gen_ai.agent.name"] == "general"  # fell back to subagent_type


async def test_subagent_idempotent(exporter):
    """create_subagent called twice on same instance → invoke not double-wrapped."""
    tracer = trace.get_tracer("t")
    metrics = Mock()
    instrument_subagent(tracer, metrics, deep_agent_cls=_FakeDeepAgent)
    parent = _FakeDeepAgent()
    sub = parent.create_subagent("research", "sub-sess-3")
    # simulate a second create_subagent returning the same instance
    # (the real DeepAgent does this for pre-built specs)
    parent.create_subagent = lambda *a: sub  # type: ignore
    sub2 = parent.create_subagent("research", "sub-sess-3")
    await sub2.invoke({"query": "hi"})
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.subagent.invoke"]
    assert len(spans) == 1  # not double-counted


async def test_subagent_error_sets_error_status(exporter):
    tracer = trace.get_tracer("t")
    metrics = Mock()

    class _FakeErr(_FakeDeepAgent):
        def create_subagent(self, subagent_type, subsession_id):
            class _SubAgent:
                card = _Card()
                async def invoke(self, inputs, session=None, **kw):
                    raise RuntimeError("subagent failed")
            return _SubAgent()

    instrument_subagent(tracer, metrics, deep_agent_cls=_FakeErr)
    parent = _FakeErr()
    sub = parent.create_subagent("research", "sub-sess-4")
    import pytest
    with pytest.raises(RuntimeError):
        await sub.invoke({"query": "hi"})
    sp = [s for s in exporter.spans if s.name == "jiuwenclaw.subagent.invoke"][0]
    assert sp.status.status_code == StatusCode.ERROR


async def test_subagent_error_result_type_sets_error_status(exporter):
    """result_type == "error" → span ERROR status (not an exception)."""
    tracer = trace.get_tracer("t")
    metrics = Mock()

    class _FakeErrResult(_FakeDeepAgent):
        def create_subagent(self, subagent_type, subsession_id):
            class _SubAgent:
                card = _Card()
                async def invoke(self, inputs, session=None, **kw):
                    return {"result_type": "error", "output": "boom"}
            return _SubAgent()

    instrument_subagent(tracer, metrics, deep_agent_cls=_FakeErrResult)
    parent = _FakeErrResult()
    sub = parent.create_subagent("research", "sub-sess-6")
    await sub.invoke({"query": "hi"})
    sp = [s for s in exporter.spans if s.name == "jiuwenclaw.subagent.invoke"][0]
    assert sp.status.status_code == StatusCode.ERROR
