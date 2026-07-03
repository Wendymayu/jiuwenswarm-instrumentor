# tests/instrumentors/test_agent.py
from unittest.mock import Mock
import json
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from jiuwenswarm_instrumentor.instrumentors.agent import instrument_agent
from jiuwenswarm_instrumentor.metrics import Metrics


def _user_input_content(span):
    """Parse gen_ai.input.messages from the span → the single user message's text."""
    raw = span.attributes["gen_ai.input.messages"]
    entries = json.loads(raw)
    return entries[0]["parts"][0]["content"]


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


async def test_react_iteration_count(exporter):
    """agent.invoke span records jiuwenclaw.agent.iterations = number of LLM calls within."""
    from jiuwenswarm_instrumentor.instrumentors.llm import instrument_llm

    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())

    class _Usage:
        input_tokens = 10; output_tokens = 5; total_tokens = 15; cache_tokens = 0
    class _Assistant:
        content = "hi"; usage_metadata = _Usage(); finish_reason = "stop"
        tool_calls = None; reasoning_content = None
    class _ModelConfig:
        model_name = "gpt-x"; temperature = 0.7; top_p = None
    class _ClientConfig:
        client_provider = "OpenAI"

    class FakeLLM:
        model_config = _ModelConfig()
        model_client_config = _ClientConfig()
        async def invoke(self, messages, *, tools=None, temperature=None, top_p=None,
                        model=None, max_tokens=None, stop=None, output_parser=None,
                        timeout=None, **kw):
            return _Assistant()

    class _AgentCard:
        id = "a1"; name = "test_agent"
    class FakeAgent:
        card = _AgentCard()
        async def invoke(self, inputs, session=None, **kw):
            llm = FakeLLM()
            for _ in range(3):
                await llm.invoke([{"role": "user", "content": "hi"}])
            return {"result_type": "success"}

    instrument_llm(tracer, metrics, model_client_cls=FakeLLM)
    instrument_agent(tracer, metrics, agent_cls=FakeAgent)
    await FakeAgent().invoke("test")
    agent_span = [s for s in exporter.spans if s.name == "jiuwenclaw.agent.invoke"][0]
    assert agent_span.attributes["jiuwenclaw.agent.iterations"] == 3


async def test_agent_records_user_input(exporter):
    """agent.invoke root span carries this turn's user input as gen_ai.input.messages
    (standard OTel GenAI attribute; single user message, JSON array)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake, log_messages=True, message_max_length=4096)
    await Fake().invoke({"query": "hello jiuwen"}, session=_Session())
    span = exporter.spans[0]
    entries = json.loads(span.attributes["gen_ai.input.messages"])
    assert entries == [{"role": "user",
                        "parts": [{"type": "text", "content": "hello jiuwen"}]}]


async def test_agent_records_user_input_string(exporter):
    """Bare-string inputs are recorded verbatim inside the user message."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake, log_messages=True)
    await Fake().invoke("ping", session=_Session())
    assert _user_input_content(exporter.spans[0]) == "ping"


async def test_agent_records_user_input_from_messages(exporter):
    """List-of-messages inputs surface the user-role message(s)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake, log_messages=True)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what is 1+1"},
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "thanks"},
    ]
    await Fake().invoke(msgs, session=_Session())
    assert _user_input_content(exporter.spans[0]) == "what is 1+1\nthanks"


async def test_agent_user_input_truncated(exporter):
    """Long inputs are capped at message_max_length (on the content, not the JSON)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake, log_messages=True, message_max_length=10)
    await Fake().invoke("x" * 200, session=_Session())
    content = _user_input_content(exporter.spans[0])
    assert len(content) == 10
    assert content.endswith("...")


async def test_agent_user_input_disabled_when_log_messages_off(exporter):
    """When log_messages is False, no user input is captured (privacy)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake, log_messages=False)
    await Fake().invoke("secret", session=_Session())
    assert "gen_ai.input.messages" not in exporter.spans[0].attributes
