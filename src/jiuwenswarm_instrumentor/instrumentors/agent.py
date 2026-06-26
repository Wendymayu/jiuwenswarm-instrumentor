# src/jiuwenswarm_instrumentor/instrumentors/agent.py
from __future__ import annotations
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs, _react_counter
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def _session_id(session):
    try:
        return session.get_session_id() if session is not None else None
    except Exception:
        return None


def instrument_agent(tracer, metrics, *, agent_cls=None):
    """Wrap ReActAgent.invoke (openjiuwen 0.1.10, react_agent.py:1506).

    NOTE: openjiuwen's BaseAgent metaclass rebinds invoke as a per-instance attribute at
    construction, so this patch MUST be applied before any agent instance is built.
    Activation (activate.py) runs at process start, before jiuwenclaw constructs agents.
    """
    if agent_cls is None:
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
        agent_cls = ReActAgent

    def factory(original):
        async def traced(self, inputs, session=None, **kwargs):
            card = getattr(self, "card", None)
            agent_id = getattr(card, "id", "")
            agent_name = getattr(card, "name", "")
            sid = _session_id(session)
            ctx_token = set_request_context(session_id=sid, agent_name=agent_name)
            attrs = {
                A.GEN_AI_AGENT_NAME: agent_name,
                A.GEN_AI_CONVERSATION_ID: sid or "",
            }
            attrs.update(current_request_attrs())
            start = time.monotonic()
            react_token = _react_counter.set(0)
            try:
                with tracer.start_as_current_span("jiuwenclaw.agent.invoke", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                    try:
                        result = await original(self, inputs, session, **kwargs)
                        rt = result.get("result_type") if isinstance(result, dict) else None
                        if rt == "error":
                            span.set_status(StatusCode.ERROR)
                        else:
                            span.set_status(StatusCode.OK)
                        span.set_attribute(A.JIUWENCLAW_AGENT_ITERATIONS, _react_counter.get() or 0)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc)[:256])
                        span.record_exception(exc)
                        span.set_attribute(A.JIUWENCLAW_AGENT_ITERATIONS, _react_counter.get() or 0)
                        raise
            finally:
                _react_counter.reset(react_token)
                ctx_token.reset()
                metrics.record_agent_duration(time.monotonic() - start,
                                               {A.GEN_AI_AGENT_NAME: agent_name})
        return traced

    patch_method(agent_cls, "invoke", factory)
