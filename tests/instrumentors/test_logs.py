# tests/instrumentors/test_logs.py
import logging

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter, SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider

from jiuwenswarm_instrumentor.instrumentors.logs import OTelLogHandler


@pytest.fixture
def otel_logger():
    exporter = InMemoryLogRecordExporter()
    lp = LoggerProvider()
    lp.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    yield lp.get_logger("test"), exporter


@pytest.fixture
def clean_jiuwenclaw_logger():
    jl = logging.getLogger("jiuwenclaw")
    saved = (jl.handlers[:], jl.level, jl.propagate)
    jl.handlers = []
    jl.setLevel(logging.INFO)
    jl.propagate = False
    yield jl
    jl.handlers = saved[0]
    jl.level = saved[1]
    jl.propagate = saved[2]


def test_emits_severity_and_body(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").warning("hello %s", "world")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    lr = logs[0].log_record
    assert lr.severity_text == "WARN"
    assert lr.severity_number == SeverityNumber.WARN
    assert lr.body == "hello world"


def test_trace_correlation(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("outside")  # no active span
    tp = TracerProvider()
    tracer = tp.get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        sc = span.get_span_context()
        logging.getLogger("jiuwenclaw").info("inside")
    logs = exporter.get_finished_logs()
    assert len(logs) == 2
    assert not logs[0].log_record.trace_id  # outside span -> 0/invalid
    assert logs[1].log_record.trace_id == sc.trace_id  # inside span -> correlated


def test_extra_attributes(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("msg", extra={"user_visible": "progress", "host": "h1"})
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.attributes["user_visible"] == "progress"
    assert lr.attributes["host"] == "h1"
    assert lr.attributes["log.logger"] == "jiuwenclaw"
    assert lr.attributes["code.function"] == "test_extra_attributes"


def test_event_name(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("msg", extra={"event_name": "msg.received"})
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.attributes["event.name"] == "msg.received"
    assert lr.event_name == "msg.received"  # native field for Phoenix


def test_excluded_logger(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    h = OTelLogHandler(lp_logger, level="INFO", excluded_loggers=("jiuwenclaw.interface.resp",))
    clean_jiuwenclaw_logger.addHandler(h)
    logging.getLogger("jiuwenclaw.interface.resp").info("resp line")  # excluded
    logging.getLogger("jiuwenclaw").info("kept")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    assert logs[0].log_record.attributes["log.logger"] == "jiuwenclaw"


def test_emit_never_raises(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    class _Broken:
        def emit(self, *a, **k):
            raise RuntimeError("boom")
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(_Broken(), level="INFO"))
    logging.getLogger("jiuwenclaw").info("ok")  # must not raise
    assert exporter.get_finished_logs() == ()
