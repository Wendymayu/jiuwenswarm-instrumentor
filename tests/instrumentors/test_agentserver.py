# tests/instrumentors/test_agentserver.py
from opentelemetry import trace
from opentelemetry.propagate import inject
from jiuwenswarm_instrumentor.instrumentors.agentserver import instrument_agentserver


class FakeRequest:
    def __init__(self, metadata=None):
        self.metadata = metadata


class FakeAdapter:
    """The 'original' process_message_impl simulates agent.invoke by starting a 'child' span."""
    async def process_message_impl(self, request, inputs):
        with trace.get_tracer("t").start_as_current_span("child"):
            pass
        return "ok"

    async def process_message_stream_impl(self, request, inputs):
        with trace.get_tracer("t").start_as_current_span("child"):
            yield "chunk"


async def test_extract_attaches_remote_parent(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    # simulate gateway: create a remote parent span + inject its traceparent into a carrier
    with tracer.start_as_current_span("remote_parent") as remote:
        carrier = {}
        inject(carrier)
    remote_ctx = remote.get_span_context()
    request = FakeRequest(metadata=carrier)
    await FakeAdapter().process_message_impl(request, {})
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.context.trace_id == remote_ctx.trace_id
    assert child.parent is not None
    assert child.parent.span_id == remote_ctx.span_id


async def test_no_metadata_no_attach(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    request = FakeRequest(metadata=None)
    await FakeAdapter().process_message_impl(request, {})
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.parent is None  # root span — no remote parent


async def test_stream_extract_attaches(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    with tracer.start_as_current_span("remote_parent") as remote:
        carrier = {}
        inject(carrier)
    remote_ctx = remote.get_span_context()
    request = FakeRequest(metadata=carrier)
    chunks = [c async for c in FakeAdapter().process_message_stream_impl(request, {})]
    assert chunks == ["chunk"]
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.context.trace_id == remote_ctx.trace_id
    assert child.parent.span_id == remote_ctx.span_id
