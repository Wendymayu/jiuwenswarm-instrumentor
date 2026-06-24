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
