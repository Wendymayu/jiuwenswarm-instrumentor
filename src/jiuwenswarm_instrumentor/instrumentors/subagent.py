# src/jiuwenswarm_instrumentor/instrumentors/subagent.py
from __future__ import annotations
import logging
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs, increment_react_counter, _react_counter
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind

logger = logging.getLogger("jiuwenswarm_instrumentor")


def instrument_subagent(tracer, metrics, *, deep_agent_cls=None):
    """Wrap DeepAgent.create_subagent to re-wrap the returned instance's invoke
    with a jiuwenclaw.subagent.invoke span + correct agent_name in request context.

    This bypasses the _AgentMeta per-instance invoke rebind (which shadows our
    class-level ReActAgent.invoke patch for runtime-constructed subagents).
    create_subagent is a plain method (not metaclass-rebound) → class-level patch works.
    """
    if deep_agent_cls is None:
        try:
            from openjiuwen.harness.deep_agent import DeepAgent
            deep_agent_cls = DeepAgent
        except Exception:
            logger.warning("[instrumentor] openjiuwen.harness.deep_agent unavailable — skipping subagent patch")
            return

    def factory(original):
        def traced(self, subagent_type, subsession_id):
            subagent = original(self, subagent_type, subsession_id)
            try:
                _rewrap_subagent_invoke(tracer, metrics, subagent, subagent_type, subsession_id)
            except Exception:
                logger.debug("[instrumentor] subagent invoke re-wrap failed", exc_info=True)
            return subagent
        return traced

    patch_method(deep_agent_cls, "create_subagent", factory)


def _rewrap_subagent_invoke(tracer, metrics, subagent, subagent_type, subsession_id):
    """Re-wrap the subagent's (already metaclass-decorated) invoke with our span."""
    if getattr(subagent, "_jiuwenswarm_subagent_wrapped", False):
        return  # idempotent
    card = getattr(subagent, "card", None)
    agent_name = getattr(card, "name", "") or subagent_type
    agent_id = getattr(card, "id", "")
    original_invoke = subagent.invoke  # the metaclass-wrapped instance attribute

    async def traced_invoke(inputs, session=None, **kwargs):
        ctx_token = set_request_context(session_id=subsession_id, agent_name=agent_name)
        attrs = {
            A.GEN_AI_AGENT_NAME: agent_name,
            A.GEN_AI_CONVERSATION_ID: subsession_id or "",
        }
        attrs.update(current_request_attrs())
        start = time.monotonic()
        react_token = _react_counter.set(0)
        try:
            with tracer.start_as_current_span("jiuwenclaw.subagent.invoke", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original_invoke(inputs, session, **kwargs)
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

    subagent.invoke = traced_invoke  # re-bind the instance attribute
    subagent._jiuwenswarm_subagent_wrapped = True
