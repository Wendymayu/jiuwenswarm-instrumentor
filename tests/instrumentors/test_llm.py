# tests/instrumentors/test_llm.py
from unittest.mock import Mock
import json
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


class _TC:
    """A tool-call delta as a stream chunk would carry it."""
    def __init__(self, idx, id, name, args):
        self.index = idx; self.id = id; self.name = name; self.arguments = args


class _Chunk:
    """Minimal stream chunk: content (text) + optional tool_calls + usage + finish."""
    def __init__(self, content="", tool_calls=None, usage=None, finish="stop"):
        self.content = content
        self.tool_calls = tool_calls
        self.usage_metadata = usage
        self.finish_reason = finish
        self.reasoning_content = None


async def test_stream_records_text_and_tool_calls_together(exporter):
    """ReAct case: the LLM streams BOTH assistant text AND a tool_call (finish
    reason tool_calls). gen_ai.output.messages must contain BOTH the text content
    and the tool_calls (the LLM's tool-call INTENT: name + arguments) — not drop
    one when the other is present (regression: text branch used to null tool_calls)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())

    class _ModelConfig:
        model_name = "gpt-x"; temperature = 0.7; top_p = None
    class _ClientConfig:
        client_provider = "OpenAI"

    class FakeModelClient:
        model_config = _ModelConfig()
        model_client_config = _ClientConfig()

        async def stream(self, messages, **kw):
            # chunk 1: assistant text + a tool_call delta
            yield _Chunk(content="let me check cpu",
                         tool_calls=[_TC(0, "call_1", "powershell", '{"command":"Get-Cpu"}')],
                         finish="tool_calls")
            # chunk 2: final usage, no further content
            yield _Chunk(content="", tool_calls=None, usage=_Usage(5, 3, 8),
                         finish="tool_calls")

    instrument_llm(tracer, metrics, log_messages=True, message_max_length=4096,
                   model_client_cls=FakeModelClient)

    async for _ in FakeModelClient().stream([{"role": "user", "content": "cpu?"}]):
        pass

    span = exporter.spans[0]
    assert span.attributes["gen_ai.response.finish_reason"] == "tool_calls"
    out = json.loads(span.attributes["gen_ai.output.messages"])
    assert out[0]["role"] == "assistant"
    # text content preserved
    assert out[0]["parts"][0]["content"] == "let me check cpu"
    # tool-call INTENT preserved (name + id + arguments)
    tc = out[0]["tool_calls"][0]
    assert tc["name"] == "powershell"
    assert tc["id"] == "call_1"
    assert "Get-Cpu" in tc["arguments"]


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


async def test_session_memory_update_labeled(exporter):
    """System prompt starting with 'You are a session memory updater' → gen_ai.operation.name=session_memory_update."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "You are a session memory updater. Your only task is to update a markdown notes file."},
        {"role": "user", "content": "update"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "session_memory_update"


async def test_full_compact_summary_labeled(exporter):
    """System prompt starting with 'Your task is to create a detailed summary' → gen_ai.operation.name=full_compact_summary."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "Your task is to create a detailed summary of the conversation so far."},
        {"role": "user", "content": "summarize"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "full_compact_summary"


async def test_normal_chat_not_labeled(exporter):
    """Normal system prompt → gen_ai.operation.name stays 'chat' (default)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hi"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "chat"
