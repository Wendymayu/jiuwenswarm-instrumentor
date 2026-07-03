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
    # exporters default to otlp (dev convenience: only OTEL_ENABLED needed)
    assert cfg.traces_exporter == "otlp"
    assert cfg.metrics_exporter == "otlp"
    assert cfg.protocol == "grpc"
    assert cfg.traces_endpoint == "http://localhost:4317"  # grpc port matches default protocol
    assert cfg.log_messages is True  # default captures prompt/response


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
    assert cfg.log_messages is True  # default; OTEL_LOG_MESSAGES not overridden here


def test_generic_headers_with_signal_overlay():
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Bearer secret,Common=val"
    os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = "X-Trace=1"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_EXPORTER_OTLP_HEADERS"]
        del os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"]
    # generic headers are the base; signal-specific overlay on top
    assert cfg.traces_headers == {"Authorization": "Bearer secret", "Common": "val", "X-Trace": "1"}
    assert cfg.metrics_headers == {"Authorization": "Bearer secret", "Common": "val"}


def test_only_enabled_yields_working_otlp_defaults():
    """Dev DX: setting ONLY OTEL_ENABLED=true must produce a complete working config
    (otlp exporters, grpc to 4317, log_messages on) — no other vars required for a
    local grpc backend (labubu/Phoenix)."""
    os.environ["OTEL_ENABLED"] = "true"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_ENABLED"]
    assert cfg.enabled is True
    assert cfg.traces_exporter == "otlp"
    assert cfg.metrics_exporter == "otlp"
    assert cfg.traces_protocol == "grpc"
    assert cfg.traces_endpoint == "http://localhost:4317"
    assert cfg.log_messages is True


def test_log_messages_can_be_disabled():
    """Privacy opt-out: OTEL_LOG_MESSAGES=false turns off prompt/response capture."""
    os.environ["OTEL_LOG_MESSAGES"] = "false"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_LOG_MESSAGES"]
    assert cfg.log_messages is False


def test_logs_config_defaults():
    cfg = load_config()
    assert cfg.logs_exporter == "otlp"   # default on (stdlib logs to backend)
    assert cfg.logs_protocol == "grpc"
    assert cfg.log_level == "INFO"
    assert cfg.log_excluded_loggers == ("jiuwenclaw.interface.resp",)
    assert cfg.log_message_max_length == 8192


def test_logs_env_overrides():
    os.environ["OTEL_LOGS_EXPORTER"] = "otlp"
    os.environ["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] = "http"
    os.environ["OTEL_LOGS_LEVEL"] = "debug"
    os.environ["OTEL_LOGS_EXCLUDED_LOGGERS"] = "a,b"
    os.environ["OTEL_LOG_MESSAGE_MAX_LENGTH"] = "100"
    try:
        cfg = load_config()
    finally:
        for k in ("OTEL_LOGS_EXPORTER", "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
                  "OTEL_LOGS_LEVEL", "OTEL_LOGS_EXCLUDED_LOGGERS",
                  "OTEL_LOG_MESSAGE_MAX_LENGTH"):
            del os.environ[k]
    assert cfg.logs_exporter == "otlp"
    assert cfg.logs_protocol == "http"
    assert cfg.log_level == "DEBUG"
    assert cfg.log_excluded_loggers == ("a", "b")
    assert cfg.log_message_max_length == 100


def test_instrument_gateway_default_false():
    """Gateway instrumentation is OFF by default — gateway data is tangential to agent
    traces. Opt in with OTEL_INSTRUMENT_GATEWAY=true."""
    cfg = load_config()
    assert cfg.instrument_gateway is False


def test_instrument_gateway_can_be_enabled():
    """OTEL_INSTRUMENT_GATEWAY=true opts into gateway spans + traceparent inject."""
    os.environ["OTEL_INSTRUMENT_GATEWAY"] = "true"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_INSTRUMENT_GATEWAY"]
    assert cfg.instrument_gateway is True
