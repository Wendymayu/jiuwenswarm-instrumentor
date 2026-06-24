from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry import trace


class CollectingSpanExporter(SpanExporter):
    """Collects spans in a list for test assertions."""
    def __init__(self):
        self.spans = []
    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass


def reset_tracing(exporter):
    """Install a fresh global TracerProvider that feeds `exporter`. Returns the exporter."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


@pytest.fixture
def exporter():
    exp = CollectingSpanExporter()
    try:
        trace.set_tracer_provider(TracerProvider())
    except Exception:
        pass
    tp = trace.get_tracer_provider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    return exp
