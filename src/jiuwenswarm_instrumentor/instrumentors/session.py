# src/jiuwenswarm_instrumentor/instrumentors/session.py
from __future__ import annotations

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def instrument_session(tracer, metrics, *, jiuwenswarm_cls=None):
    """Wrap JiuWenSwarm.create_instance + .cleanup (jiuwenswarm develop, interface.py)."""
    if jiuwenswarm_cls is None:
        from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
        jiuwenswarm_cls = JiuWenSwarm

    def create_factory(original):
        async def traced(self, *args, **kw):
            # signature-agnostic: develop's create_instance(self, config=None, *, mode="agent",
            # sub_mode=None) has no session_id param. Set a placeholder request context, then
            # read the real session id back from the instance after the call (like cleanup).
            ctx_token = set_request_context(session_id="")
            attrs = {"jiuwenclaw.session.mode": kw.get("mode", "agent")}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.create", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, *args, **kw)
                    sid = getattr(self, "_session_id", None)
                    span.set_attribute(A.JIUWENCLAW_SESSION_ID, sid or "")
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
                finally:
                    try:
                        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
                        skill_state.clear_session(sid or "")
                    except Exception:
                        pass
        return traced

    patch_method(jiuwenswarm_cls, "create_instance", create_factory)
    patch_method(jiuwenswarm_cls, "cleanup", cleanup_factory)
