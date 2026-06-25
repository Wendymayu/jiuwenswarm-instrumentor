# tests/instrumentors/test_logs.py
import logging
import sys
import types

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter, SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider

from jiuwenswarm_instrumentor.instrumentors.logs import OTelLogHandler, instrument_logs


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


def test_filter_piggyback(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    class RedactFilter(logging.Filter):
        def filter(self, record):
            record.msg = record.msg.replace("secret", "***")
            return True
    pre = logging.StreamHandler()
    pre.addFilter(RedactFilter())
    clean_jiuwenclaw_logger.addHandler(pre)
    instrument_logs(otel_logger=lp_logger, level="INFO")
    # our handler copied the filter; prove the COPY redacts in isolation by removing `pre`
    clean_jiuwenclaw_logger.removeHandler(pre)
    logging.getLogger("jiuwenclaw").info("hello secret world")
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.body == "hello *** world"
    ours = [h for h in clean_jiuwenclaw_logger.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1
    assert any(isinstance(f, RedactFilter) for f in ours[0].filters)


def test_no_filters_warning_fallback(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    instrument_logs(otel_logger=lp_logger, level="INFO")  # no pre-existing filters
    logging.getLogger("jiuwenclaw").info("dropped")
    logging.getLogger("jiuwenclaw").warning("kept")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    assert logs[0].log_record.severity_text == "WARN"


def test_setup_logger_reattach(otel_logger, clean_jiuwenclaw_logger, monkeypatch):
    lp_logger, exporter = otel_logger
    jl = logging.getLogger("jiuwenclaw")
    class RedactFilter(logging.Filter):
        def filter(self, record):
            return True

    def fake_setup_logger():
        # realistic: clear then re-add an app handler carrying a redaction filter
        jl.handlers = []
        app_h = logging.StreamHandler()
        app_h.addFilter(RedactFilter())
        jl.addHandler(app_h)

    fake_mod = types.ModuleType("jiuwenclaw.utils")
    fake_mod.setup_logger = fake_setup_logger
    pkg = types.ModuleType("jiuwenclaw")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "jiuwenclaw", pkg)
    monkeypatch.setitem(sys.modules, "jiuwenclaw.utils", fake_mod)

    instrument_logs(otel_logger=lp_logger, level="INFO")
    assert any(getattr(h, "_jiuwenswarm_otel", False) for h in jl.handlers)
    fake_mod.setup_logger()  # wrapped: clears+re-adds app handler, then re-attaches ours
    ours = [h for h in jl.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1
    assert ours[0].level == logging.INFO  # redaction present -> INFO retained
    assert any(isinstance(f, RedactFilter) for f in ours[0].filters)  # filter piggybacked


def test_idempotent(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    instrument_logs(otel_logger=lp_logger, level="INFO")
    instrument_logs(otel_logger=lp_logger, level="INFO")
    ours = [h for h in clean_jiuwenclaw_logger.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1


def test_setup_logger_clear_only_keeps_info(otel_logger, clean_jiuwenclaw_logger, monkeypatch):
    """Clear-only setup_logger (no app filters re-added) must keep INFO because our
    handler retains its redaction filters from the first attach (regression for the
    level-downgrade bug)."""
    lp_logger, exporter = otel_logger
    jl = logging.getLogger("jiuwenclaw")
    class RedactFilter(logging.Filter):
        def filter(self, record):
            return True

    # clear-only setup_logger: clears handlers, does NOT re-add any app handler/filter
    def fake_setup_logger():
        jl.handlers = []
    fake_mod = types.ModuleType("jiuwenclaw.utils")
    fake_mod.setup_logger = fake_setup_logger
    pkg = types.ModuleType("jiuwenclaw")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "jiuwenclaw", pkg)
    monkeypatch.setitem(sys.modules, "jiuwenclaw.utils", fake_mod)

    # first attach: app has a redaction filter -> our handler copies it, level INFO.
    # (fake modules must be in sys.modules BEFORE instrument_logs so the patch wraps
    # fake_mod.setup_logger; otherwise the import inside _patch_setup_logger_to_reattach
    # fails and the wrap is a no-op.)
    pre = logging.StreamHandler()
    pre.addFilter(RedactFilter())
    jl.addHandler(pre)
    instrument_logs(otel_logger=lp_logger, level="INFO")
    ours = [h for h in jl.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert ours[0].level == logging.INFO

    fake_mod.setup_logger()  # wrapped: clears (no re-add), then re-attaches ours
    ours = [h for h in jl.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1
    assert ours[0].level == logging.INFO  # NOT downgraded (handler retains filters)
    assert any(isinstance(f, RedactFilter) for f in ours[0].filters)
