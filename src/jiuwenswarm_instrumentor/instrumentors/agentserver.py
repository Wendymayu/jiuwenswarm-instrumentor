# src/jiuwenswarm_instrumentor/instrumentors/agentserver.py
from __future__ import annotations

import logging

from opentelemetry import context, trace
from opentelemetry.propagate import extract

from jiuwenswarm_instrumentor.wrap import patch_method

logger = logging.getLogger("jiuwenswarm_instrumentor")


def _attach_remote_parent(request):
    """Extract W3C traceparent from request.metadata + attach as current context.
    Returns a detach token (or None if no valid remote parent). Fail-soft.
    Must be called in process_message_impl[_stream] (same task as ReActAgent.invoke)."""
    try:
        carrier = request.metadata if isinstance(request.metadata, dict) else {}
        extracted = extract(carrier)
        remote = trace.get_current_span(extracted).get_span_context()
        if remote is not None and remote.is_valid:
            return context.attach(extracted)
    except Exception:
        pass
    return None


def instrument_agentserver(tracer, *, adapter_cls=None):
    """Wrap JiuWenClawDeepAdapter.process_message_impl[_stream] to extract the gateway's
    traceparent from request.metadata + context.attach it, so the existing agent.invoke span
    (start_as_current_span in agent.py) becomes a child of the gateway CLIENT span.
    NOTE: wrap the _impl methods (not the public process_message) — the public method and
    ReActAgent.invoke are separated by the session-manager queue (task boundary); _impl and
    ReActAgent.invoke share the same task, so context.attach survives.
    Fail-soft: skip if adapter import fails (test env without jiuwenclaw)."""
    if adapter_cls is None:
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
            adapter_cls = JiuWenSwarmDeepAdapter
        except Exception:
            logger.warning("[instrumentor] jiuwenswarm.server.runtime.agent_adapter.deep_agent unavailable — skipping JiuWenSwarmDeepAdapter patch")
            adapter_cls = None
    if adapter_cls is None:
        return

    def factory_impl(original):
        async def traced(self, request, inputs):
            token = _attach_remote_parent(request)
            try:
                return await original(self, request, inputs)
            finally:
                if token is not None:
                    context.detach(token)
        return traced

    def factory_impl_stream(original):
        async def traced(self, request, inputs):
            token = _attach_remote_parent(request)
            try:
                async for chunk in original(self, request, inputs):
                    yield chunk
            finally:
                if token is not None:
                    context.detach(token)
        return traced

    patch_method(adapter_cls, "process_message_impl", factory_impl)
    patch_method(adapter_cls, "process_message_stream_impl", factory_impl_stream)
