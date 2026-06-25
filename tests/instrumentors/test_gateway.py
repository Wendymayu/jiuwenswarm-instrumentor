# tests/instrumentors/test_gateway.py
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.gateway import instrument_gateway


class FakeEnvelope:
    def __init__(self, channel_context=None):
        self.channel_context = channel_context


class FakeAgentClient:
    async def send_request(self, envelope):
        return "resp"

    async def send_request_stream(self, envelope):
        yield "chunk1"
        yield "chunk2"


class FakeMessageHandler:
    """Real MessageHandler.process_stream is a COROUTINE (caller passes it to
    asyncio.create_task), not an async generator — so the fake matches that."""
    def __init__(self, ac=None):
        self._ac = ac or FakeAgentClient()

    async def process_stream(self, *args, **kw):
        async for _c in self._ac.send_request_stream(FakeEnvelope({})):
            pass  # consume the stream
        return "done"


async def test_send_request_injects_traceparent(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context={})
    result = await FakeAgentClient().send_request(env)
    assert result == "resp"
    assert "traceparent" in env.channel_context
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request"]
    assert len(spans) == 1
    assert spans[0].kind == trace.SpanKind.CLIENT


async def test_send_request_stream_injects_traceparent(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context={})
    chunks = [c async for c in FakeAgentClient().send_request_stream(env)]
    assert chunks == ["chunk1", "chunk2"]
    assert "traceparent" in env.channel_context
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request"]
    assert len(spans) == 1


async def test_inject_failsoft_no_channel_context(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context=None)
    result = await FakeAgentClient().send_request(env)  # must not raise
    assert result == "resp"
    assert isinstance(env.channel_context, dict)  # promoted from None
    assert "traceparent" in env.channel_context  # inject still ran on the fresh dict


async def test_process_stream_creates_channel_request_span(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, message_handler_cls=FakeMessageHandler, agent_client_cls=FakeAgentClient)
    mh = FakeMessageHandler()
    result = await mh.process_stream()  # process_stream is a coroutine, not an async gen
    assert result == "done"
    spans = [s for s in exporter.spans if s.name == "channel.request"]
    assert len(spans) == 1


async def test_channel_request_is_parent_of_client(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, message_handler_cls=FakeMessageHandler, agent_client_cls=FakeAgentClient)
    mh = FakeMessageHandler()
    await mh.process_stream()
    cr = next(s for s in exporter.spans if s.name == "channel.request")
    cl = next(s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request")
    assert cl.parent is not None
    assert cl.parent.span_id == cr.context.span_id
    assert cl.context.trace_id == cr.context.trace_id
