# tests/instrumentors/test_context_compaction.py
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.context_compaction import instrument_context_compaction


class FakeMsg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeCounter:
    def count_messages(self, msgs):
        return sum(len(getattr(m, "content", "") or "") for m in (msgs or []))


class FakeSessionContext:
    def __init__(self, messages=None):
        self._msgs = messages or []
    def get_messages(self):
        return self._msgs
    def token_counter(self):
        return FakeCounter()
    def session_id(self):
        return "s1"
    def context_id(self):
        return "c1"


class FakeMetrics:
    def __init__(self):
        self.calls = []
    def record_context_compaction(self, count, tokens_saved, attrs):
        self.calls.append({"count": count, "tokens_saved": tokens_saved, "attrs": attrs})


class FakeWindow:
    def __init__(self, messages):
        self.messages = messages


class FakeGetProcessor:
    def processor_type(self):
        return "FakeGetProcessor"
    async def on_get_context_window(self, context, context_window, **kw):
        # 模拟压缩:砍掉一半消息
        half = len(context_window.messages) // 2
        context_window.messages = context_window.messages[:half]
        return None, context_window


class FakeGetProcessorNoOp:
    def processor_type(self):
        return "FakeGetProcessorNoOp"
    async def on_get_context_window(self, context, context_window, **kw):
        return None, context_window  # 不压缩


async def test_add_compaction_emits_span_metric(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            self._msgs = self._msgs[:len(self._msgs) // 2]  # 模拟压缩
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await sc.add_messages([])
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1
    assert spans[0].attributes["context.compaction.path"] == "ADD"
    assert spans[0].attributes["context.compaction.tokens_saved"] > 0
    assert spans[0].attributes["jiuwenclaw.session.id"] == "s1"
    assert len(fm.calls) == 1
    assert fm.calls[0]["attrs"]["context.compaction.path"] == "ADD"


async def test_add_no_compaction_no_emit(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            pass  # buffer 不变
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello")])
    await sc.add_messages([])
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0
    assert len(fm.calls) == 0


async def test_get_per_processor_compaction(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessor])
    proc = FakeGetProcessor()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await proc.on_get_context_window(ctx, window)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1
    assert spans[0].attributes["context.compaction.path"] == "GET"
    assert spans[0].attributes["context.compaction.processor_type"] == "FakeGetProcessor"
    assert spans[0].attributes["context.compaction.tokens_saved"] > 0
    assert len(fm.calls) == 1


async def test_get_no_compaction_no_emit(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessorNoOp])
    proc = FakeGetProcessorNoOp()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "hello")])
    await proc.on_get_context_window(ctx, window)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0
    assert len(fm.calls) == 0


async def test_multiple_get_processors(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None,
                                  processor_classes=[FakeGetProcessor, FakeGetProcessorNoOp])
    ctx = FakeSessionContext()
    w1 = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await FakeGetProcessor().on_get_context_window(ctx, w1)
    w2 = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await FakeGetProcessorNoOp().on_get_context_window(ctx, w2)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1  # only the compressing one
    assert spans[0].attributes["context.compaction.processor_type"] == "FakeGetProcessor"


async def test_attributes_correct(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessor])
    proc = FakeGetProcessor()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "abcdefgh"), FakeMsg("tool", "xxxxxxxx")])  # 16 tokens
    await proc.on_get_context_window(ctx, window)
    sp = [s for s in exporter.spans if s.name == "context.compaction"][0]
    assert sp.attributes["context.compaction.tokens_before"] == 16
    assert sp.attributes["context.compaction.tokens_after"] == 8  # half
    assert sp.attributes["context.compaction.tokens_saved"] == 8
    assert sp.attributes["context.compaction.messages_before"] == 2
    assert sp.attributes["context.compaction.messages_after"] == 1
    assert sp.attributes["jiuwenclaw.context.id"] == "c1"


async def test_failsoft_counter_raises(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class _BoomCtx(FakeSessionContext):
        def token_counter(self):
            raise RuntimeError("boom")
    class FakeSC(_BoomCtx):
        async def add_messages(self, messages_to_add, **kw):
            self._msgs = self._msgs[:len(self._msgs) // 2]
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello"), FakeMsg("tool", "world")])
    await sc.add_messages([])  # must not raise
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0  # counter failed → no delta → no emit


async def test_irreducible_propagates(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class IrreducibleContextError(Exception):
        pass
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            raise IrreducibleContextError("cannot compact")
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello")])
    raised = False
    try:
        await sc.add_messages([])
    except IrreducibleContextError:
        raised = True
    assert raised  # 透传,不吞
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0  # 异常 → 不 emit
