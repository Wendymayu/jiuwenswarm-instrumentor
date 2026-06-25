# tests/instrumentors/test_llm.py
from unittest.mock import Mock
import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from jiuwenswarm_instrumentor.instrumentors.llm import instrument_llm
from jiuwenswarm_instrumentor.metrics import Metrics
from jiuwenswarm_instrumentor import context


class _Usage:
    def __init__(self, i, o, t, c=0):
        self.input_tokens = i; self.output_tokens = o; self.total_tokens = t; self.cache_tokens = c


class _Assistant:
    def __init__(self, content="hi", usage=None, finish_reason="stop"):
        self.content = content
        self.usage_metadata = usage
        self.finish_reason = finish_reason
        self.tool_calls = None
        self.reasoning_content = None


def _make_fake_client_cls():
    class _ModelConfig:
        model_name = "gpt-x"; temperature = 0.7; top_p = None
    class _ClientConfig:
        client_provider = "OpenAI"

    class FakeModelClient:
        model_config = _ModelConfig()
        model_client_config = _ClientConfig()

        async def invoke(self, messages, *, tools=None, temperature=None, top_p=None,
                         model=None, max_tokens=None, stop=None, output_parser=None, timeout=None, **kw):
            return _Assistant(usage=_Usage(12, 8, 20))

        async def stream(self, messages, **kw):
            yield _Assistant(content="he", usage=None, finish_reason="null")
            yield _Assistant(content="llo", usage=_Usage(5, 3, 8), finish_reason="stop")
    return FakeModelClient


async def test_invoke_creates_genai_span(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)

    client = Fake()
    ctx_token = context.set_request_context(session_id="s1", channel_id="c1")
    try:
        await client.invoke([{"role": "user", "content": "hi"}])
    finally:
        ctx_token.reset()

    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "gen_ai.chat"
    assert span.attributes["gen_ai.request.model"] == "gpt-x"
    assert span.attributes["gen_ai.usage.input_tokens"] == 12
    assert span.attributes["gen_ai.usage.output_tokens"] == 8
    assert span.attributes["jiuwenclaw.session.id"] == "s1"
    assert span.attributes["gen_ai.usage.estimated"] is True
    assert "gen_ai.context.user_messages" in span.attributes


async def test_stream_creates_span_with_ttft_and_final_usage(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)

    chunks = []
    async for c in Fake().stream([{"role": "user", "content": "hi"}]):
        chunks.append(c)

    assert len(chunks) == 2
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "gen_ai.chat"
    assert span.attributes["gen_ai.request.streaming"] is True
    assert "gen_ai.streaming.first_token_ms" in span.attributes
    assert span.attributes["gen_ai.usage.input_tokens"] == 5
    assert span.attributes["gen_ai.usage.output_tokens"] == 3
    assert span.attributes["gen_ai.usage.estimated"] is True
    assert "gen_ai.context.user_messages" in span.attributes


async def test_invoke_exception_sets_error_and_reraises(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())

    class _ErrClient:
        class model_config:
            model_name = "gpt-x"; temperature = 0.7; top_p = None
        class model_client_config:
            client_provider = "OpenAI"
        async def invoke(self, messages, **kw):
            raise RuntimeError("boom")

    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=_ErrClient)
    with pytest.raises(RuntimeError):
        await _ErrClient().invoke([{"role": "user", "content": "hi"}])
    span = exporter.spans[0]
    assert span.name == "gen_ai.chat"
    assert span.status.status_code == StatusCode.ERROR
