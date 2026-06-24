from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentorConfig:
    enabled: bool = False
    traces_exporter: str = "none"        # otlp | console | none
    traces_endpoint: str = "http://localhost:4317"
    traces_protocol: str = "grpc"        # grpc | http
    traces_headers: dict = None
    metrics_exporter: str = "none"
    metrics_endpoint: str = "http://localhost:4317"
    metrics_protocol: str = "grpc"
    metrics_headers: dict = None
    protocol: str = "grpc"
    service_name: str = "jiuwenclaw"
    log_messages: bool = False           # opt-in full prompt/response capture
    message_max_length: int = 4096


def _str(key, default):
    v = os.getenv(key)
    return (v or "").strip() or default


def _bool(key, default):
    v = os.getenv(key)
    return default if v is None else v.strip().lower() in ("true", "1", "yes")


def _lower(key, default):
    v = os.getenv(key)
    return (v or "").strip().lower() or default


def _headers(key):
    raw = os.getenv(key, "")
    out = {}
    for item in raw.split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_config() -> InstrumentorConfig:
    protocol = _lower("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    endpoint = _str("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    return InstrumentorConfig(
        enabled=_bool("OTEL_ENABLED", False),
        traces_exporter=_lower("OTEL_TRACES_EXPORTER", "none"),
        traces_endpoint=_str("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        traces_protocol=_lower("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", protocol),
        traces_headers=_headers("OTEL_EXPORTER_OTLP_TRACES_HEADERS"),
        metrics_exporter=_lower("OTEL_METRICS_EXPORTER", "none"),
        metrics_endpoint=_str("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", endpoint),
        metrics_protocol=_lower("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", protocol),
        metrics_headers=_headers("OTEL_EXPORTER_OTLP_METRICS_HEADERS"),
        protocol=protocol,
        service_name=_str("OTEL_SERVICE_NAME", "jiuwenclaw"),
        log_messages=_bool("OTEL_LOG_MESSAGES", False),
        message_max_length=int(_str("OTEL_MESSAGE_CONTENT_MAX_LENGTH", "4096") or 4096),
    )
