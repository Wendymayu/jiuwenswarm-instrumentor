from __future__ import annotations
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

from jiuwenswarm_instrumentor.config import InstrumentorConfig

logger = logging.getLogger("jiuwenswarm_instrumentor")


def init_providers(cfg: InstrumentorConfig):
    """Build + install TracerProvider & MeterProvider. Returns (tracer_provider, meter_provider)
    or None if disabled. Fail-soft: logs and returns None on error."""
    if not cfg.enabled:
        return None
    try:
        resource = Resource.create({SERVICE_NAME: cfg.service_name, "telemetry.sdk.language": "python"})
        tp = TracerProvider(resource=resource)
        mp = MeterProvider(resource=resource, metric_readers=_metric_readers(cfg))
        _attach_traces(tp, cfg)
        try:
            trace.set_tracer_provider(tp)
        except Exception:
            pass  # already set in-process; ignore
        try:
            metrics.set_meter_provider(mp)
        except Exception:
            pass
        return tp, mp
    except Exception:
        logger.exception("[instrumentor] provider init failed — telemetry disabled")
        return None


def _attach_traces(tp, cfg):
    if cfg.traces_exporter == "otlp":
        tp.add_span_processor(BatchSpanProcessor(_otlp_span_exporter(cfg)))
    elif cfg.traces_exporter == "console":
        tp.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))


def _metric_readers(cfg):
    readers = []
    if cfg.metrics_exporter == "otlp":
        readers.append(PeriodicExportingMetricReader(_otlp_metric_exporter(cfg), export_interval_millis=30000))
    elif cfg.metrics_exporter == "console":
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=3000))
    return readers


def _otlp_span_exporter(cfg):
    if cfg.traces_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=f"{cfg.traces_endpoint}/v1/traces", headers=cfg.traces_headers)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    return OTLPSpanExporter(endpoint=cfg.traces_endpoint, headers=cfg.traces_headers)


def _otlp_metric_exporter(cfg):
    if cfg.metrics_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=f"{cfg.metrics_endpoint}/v1/metrics", headers=cfg.metrics_headers)
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    return OTLPMetricExporter(endpoint=cfg.metrics_endpoint, headers=cfg.metrics_headers)
