# src/jiuwenswarm_instrumentor/instrumentors/session.py
from __future__ import annotations

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def instrument_session(tracer, metrics, *, jiuwenclaw_cls=None):
    """Wrap JiuWenClaw.create_instance + .cleanup (jiuwenclaw enterprise_dev, interface.py)."""
    if jiuwenclaw_cls is None:
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jiuwenclaw_cls = JiuWenClaw

    def create_factory(original):
        async def traced(self, config=None, *, mode="agent", session_id=None, **kw):
            ctx_token = set_request_context(session_id=session_id)
            attrs = {A.JIUWENCLAW_SESSION_ID: session_id or "", "jiuwenclaw.session.mode": mode}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.create", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, config, mode=mode, session_id=session_id, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    ctx_token.reset()
        return traced

    def cleanup_factory(original):
        async def traced(self, *args, **kw):
            sid = getattr(self, "_session_id", None)
            attrs = {A.JIUWENCLAW_SESSION_ID: sid or ""}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.end", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, *args, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
        return traced

    patch_method(jiuwenclaw_cls, "create_instance", create_factory)
    patch_method(jiuwenclaw_cls, "cleanup", cleanup_factory)
