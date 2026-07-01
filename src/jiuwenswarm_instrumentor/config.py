from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentorConfig:
    enabled: bool = False
    traces_exporter: str = "otlp"         # otlp | console | none — default otlp so devs only set OTEL_ENABLED
    traces_endpoint: str = "http://localhost:4317"   # 4317 = grpc (matches default protocol)
    traces_protocol: str = "grpc"        # grpc | http
    traces_headers: dict = None
    metrics_exporter: str = "otlp"        # default otlp — set none to disable
    metrics_endpoint: str = "http://localhost:4317"
    metrics_protocol: str = "grpc"
    metrics_headers: dict = None
    protocol: str = "grpc"
    service_name: str = "jiuwenclaw"
    log_messages: bool = True             # default true: capture prompt/response + tool args/result (set false for privacy)
    message_max_length: int = 4096
    logs_exporter: str = "otlp"           # otlp | console | none — default otlp: jiuwenclaw stdlib logs to backend
    logs_endpoint: str = "http://localhost:4317"
    logs_protocol: str = "grpc"          # grpc | http
    logs_headers: dict = None
    log_level: str = "INFO"              # NOTSET|DEBUG|INFO|WARNING|ERROR|CRITICAL
    log_excluded_loggers: tuple = ()
    log_message_max_length: int = 8192


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
    base_headers = _headers("OTEL_EXPORTER_OTLP_HEADERS")
    return InstrumentorConfig(
        enabled=_bool("OTEL_ENABLED", False),
        traces_exporter=_lower("OTEL_TRACES_EXPORTER", "otlp"),
        traces_endpoint=_str("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        traces_protocol=_lower("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", protocol),
        traces_headers={**base_headers, **_headers("OTEL_EXPORTER_OTLP_TRACES_HEADERS")},
        metrics_exporter=_lower("OTEL_METRICS_EXPORTER", "otlp"),
        metrics_endpoint=_str("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", endpoint),
        metrics_protocol=_lower("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", protocol),
        metrics_headers={**base_headers, **_headers("OTEL_EXPORTER_OTLP_METRICS_HEADERS")},
        protocol=protocol,
        service_name=_str("OTEL_SERVICE_NAME", "jiuwenclaw"),
        log_messages=_bool("OTEL_LOG_MESSAGES", True),
        message_max_length=int(_str("OTEL_MESSAGE_CONTENT_MAX_LENGTH", "4096") or 4096),
        logs_exporter=_lower("OTEL_LOGS_EXPORTER", "otlp"),
        logs_endpoint=_str("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", endpoint),
        logs_protocol=_lower("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", protocol),
        logs_headers={**base_headers, **_headers("OTEL_EXPORTER_OTLP_LOGS_HEADERS")},
        log_level=(_str("OTEL_LOGS_LEVEL", "") or _str("OTEL_LOG_LEVEL", "INFO")).upper() or "INFO",
        log_excluded_loggers=tuple(
            s.strip() for s in _str("OTEL_LOGS_EXCLUDED_LOGGERS", "jiuwenclaw.interface.resp").split(",") if s.strip()
        ),
        log_message_max_length=int(_str("OTEL_LOG_MESSAGE_MAX_LENGTH", "8192") or 8192),
    )
