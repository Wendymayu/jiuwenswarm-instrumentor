# tests/instrumentors/test_session.py
from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.session import instrument_session
from jiuwenswarm_instrumentor.metrics import Metrics


def _fake_jiuwenclaw_cls():
    class FakeJW:
        async def create_instance(self, config=None, *, mode="agent", session_id=None):
            self._session_id = session_id
        async def cleanup(self):
            pass
    return FakeJW


async def test_session_spans(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_jiuwenclaw_cls()
    instrument_session(tracer, metrics, jiuwenclaw_cls=Fake)
    inst = Fake()
    await inst.create_instance(session_id="sess-7")
    await inst.cleanup()
    names = [s.name for s in exporter.spans]
    assert "jiuwenclaw.session.create" in names
    assert "jiuwenclaw.session.end" in names
    create_span = next(s for s in exporter.spans if s.name == "jiuwenclaw.session.create")
    assert create_span.attributes["jiuwenclaw.session.id"] == "sess-7"
