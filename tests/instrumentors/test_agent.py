# tests/instrumentors/test_agent.py
from unittest.mock import Mock
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from jiuwenswarm_instrumentor.instrumentors.agent import instrument_agent
from jiuwenswarm_instrumentor.metrics import Metrics


class _Card:
    id = "agent-1"; name = "JiuwenAgent"


class _Session:
    def get_session_id(self):
        return "sess-9"


def _fake_agent_cls():
    class FakeAgent:
        card = _Card()
        async def invoke(self, inputs, session=None, **kw):
            return {"output": "done", "result_type": "answer"}
    return FakeAgent


async def test_agent_span_and_context(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake)
    out = await Fake().invoke({"query": "hi"}, session=_Session())
    assert out["output"] == "done"
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "jiuwenclaw.agent.invoke"
    assert span.attributes["gen_ai.agent.name"] == "JiuwenAgent"
    assert span.attributes["jiuwenclaw.session.id"] == "sess-9"


async def test_agent_error_result_sets_error_status(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())

    class _ErrResultAgent:
        card = _Card()
        async def invoke(self, inputs, session=None, **kw):
            return {"output": "fail", "result_type": "error"}

    instrument_agent(tracer, metrics, agent_cls=_ErrResultAgent)
    await _ErrResultAgent().invoke({"query": "hi"}, session=_Session())
    span = exporter.spans[0]
    assert span.name == "jiuwenclaw.agent.invoke"
    assert span.status.status_code == StatusCode.ERROR
