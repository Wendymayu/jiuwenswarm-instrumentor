import os
import pytest
from jiuwenswarm_instrumentor.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("OTEL_"):
            monkeypatch.delenv(k, raising=False)


def test_disabled_by_default():
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.traces_exporter == "none"
    assert cfg.protocol == "grpc"


def test_env_overrides():
    os.environ["OTEL_ENABLED"] = "true"
    os.environ["OTEL_TRACES_EXPORTER"] = "otlp"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http"
    os.environ["OTEL_SERVICE_NAME"] = "jiuwenclaw-prod"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_ENABLED"]; del os.environ["OTEL_TRACES_EXPORTER"]
        del os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]; del os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]
        del os.environ["OTEL_SERVICE_NAME"]
    assert cfg.enabled is True
    assert cfg.traces_exporter == "otlp"
    assert cfg.traces_endpoint == "http://localhost:4317"
    assert cfg.traces_protocol == "http"
    assert cfg.service_name == "jiuwenclaw-prod"
    assert cfg.log_messages is False
