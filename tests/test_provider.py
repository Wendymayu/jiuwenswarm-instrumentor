from unittest.mock import patch
from jiuwenswarm_instrumentor.config import InstrumentorConfig
from jiuwenswarm_instrumentor import provider


def test_disabled_returns_none():
    cfg = InstrumentorConfig(enabled=False)
    assert provider.init_providers(cfg) is None


def test_otlp_http_builds_providers(monkeypatch):
    monkeypatch.setattr(provider.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(provider.metrics, "set_meter_provider", lambda p: None)
    cfg = InstrumentorConfig(
        enabled=True, traces_exporter="otlp", traces_protocol="http",
        traces_endpoint="http://localhost:4318", service_name="jc",
    )
    tp, mp = provider.init_providers(cfg)
    assert tp is not None and mp is not None


def test_logs_console_sets_logger_provider(monkeypatch):
    monkeypatch.setattr(provider.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(provider.metrics, "set_meter_provider", lambda p: None)
    captured = []
    monkeypatch.setattr(provider.logs, "set_logger_provider", lambda p: captured.append(p))
    cfg = InstrumentorConfig(enabled=True, logs_exporter="console", service_name="jc")
    tp, mp = provider.init_providers(cfg)
    assert tp is not None and mp is not None
    assert len(captured) == 1
    from opentelemetry.sdk._logs import LoggerProvider
    assert isinstance(captured[0], LoggerProvider)
    assert captured[0].resource.attributes.get("service.name") == "jc"
