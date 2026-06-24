# src/jiuwenswarm_instrumentor/instrumentors/tool.py
from __future__ import annotations
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def instrument_tool(tracer, metrics, *, ability_cls=None):
    """Wrap AbilityManager.execute_single (openjiuwen 0.1.10, ability_manager.py:635)."""
    if ability_cls is None:
        from openjiuwen.core.single_agent.ability_manager import AbilityManager
        ability_cls = AbilityManager

    def factory(original):
        async def traced(self, parent_ctx, tool_call, session, tag=None):
            name = getattr(tool_call, "name", "unknown")
            call_id = getattr(tool_call, "id", "") or ""
            attrs = {
                A.GEN_AI_TOOL_NAME: name,
                A.GEN_AI_TOOL_CALL_ID: call_id,
            }
            attrs.update(current_request_attrs())
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.tool", kind=SpanKind.CLIENT, attributes=attrs) as span:
                try:
                    result = await original(self, parent_ctx, tool_call, session, tag=tag)
                    tool_msg = result[1] if isinstance(result, tuple) and len(result) >= 2 else None
                    is_error = bool(
                        tool_msg is not None
                        and getattr(tool_msg, "metadata", None)
                        and tool_msg.metadata.get("is_error")
                    )
                    span.set_status(StatusCode.ERROR if is_error else StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    base = {A.GEN_AI_TOOL_NAME: name}
                    base.update(current_request_attrs())
                    metrics.record_tool(time.monotonic() - start, base)
        return traced

    patch_method(ability_cls, "execute_single", factory)
