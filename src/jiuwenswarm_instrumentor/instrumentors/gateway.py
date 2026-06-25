# src/jiuwenswarm_instrumentor/instrumentors/gateway.py
from __future__ import annotations
import logging

from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.wrap import patch_method

logger = logging.getLogger("jiuwenswarm_instrumentor")


def _inject_traceparent(envelope):
    """Inject W3C traceparent into envelope.channel_context.
    Must be called while the CLIENT span is current so traceparent points at it.
    Fail-soft: if channel_context isn't a dict, use a fresh {}."""
    carrier = envelope.channel_context if isinstance(envelope.channel_context, dict) else {}
    envelope.channel_context = carrier
    try:
        inject(carrier)
    except Exception:
        pass


def _envelope_attrs(envelope):
    attrs = {}
    try:
        cc = getattr(envelope, "channel_context", None)
        if isinstance(cc, dict):
            cid = cc.get("channel_id") or cc.get("channelId")
            if cid:
                attrs[A.JIUWENCLAW_CHANNEL_ID] = str(cid)
            rid = cc.get("request_id") or cc.get("requestId")
            if rid:
                attrs[A.JIUWENCLAW_REQUEST_ID] = str(rid)
    except Exception:
        pass
    return attrs


def _process_attrs(args, kw):
    """Best-effort channel_id/request_id from process_stream args (message/envelope objects)."""
    attrs = {}
    try:
        candidates = list(args) + list(kw.values())
        for obj in candidates:
            cid = getattr(obj, "channel_id", None)
            if cid and isinstance(cid, str):
                attrs[A.JIUWENCLAW_CHANNEL_ID] = cid
                break
        for obj in candidates:
            rid = getattr(obj, "request_id", None)
            if rid and isinstance(rid, str):
                attrs[A.JIUWENCLAW_REQUEST_ID] = rid
                break
    except Exception:
        pass
    return attrs


def instrument_gateway(tracer, *, message_handler_cls=None, agent_client_cls=None):
    """Wrap gateway MessageHandler (channel.request span) + WebSocketAgentServerClient
    (jiuwenclaw.gateway.agent.request CLIENT span + traceparent inject into envelope.channel_context).
    Fail-soft per class: skip a class if its import fails (test env without jiuwenclaw)."""
    # --- channel.request span (MessageHandler.process_message / process_stream) ---
    if message_handler_cls is None:
        try:
            from jiuwenclaw.gateway.message_handler import MessageHandler
            message_handler_cls = MessageHandler
        except Exception:
            logger.warning("[instrumentor] jiuwenclaw.gateway.message_handler unavailable — skipping MessageHandler patch")
            message_handler_cls = None
    if message_handler_cls is not None:
        def factory_process(original):
            async def traced(self, *args, **kw):
                attrs = _process_attrs(args, kw)
                with tracer.start_as_current_span("channel.request", kind=SpanKind.INTERNAL, attributes=attrs):
                    return await original(self, *args, **kw)
            return traced

        def factory_process_stream(original):
            async def traced(self, *args, **kw):
                attrs = _process_attrs(args, kw)
                with tracer.start_as_current_span("channel.request", kind=SpanKind.INTERNAL, attributes=attrs):
                    async for chunk in original(self, *args, **kw):
                        yield chunk
            return traced

        patch_method(message_handler_cls, "process_message", factory_process)
        patch_method(message_handler_cls, "process_stream", factory_process_stream)

    # --- jiuwenclaw.gateway.agent.request CLIENT span + inject (send_request / send_request_stream) ---
    if agent_client_cls is None:
        try:
            from jiuwenclaw.gateway.agent_client import WebSocketAgentServerClient
            agent_client_cls = WebSocketAgentServerClient
        except Exception:
            logger.warning("[instrumentor] jiuwenclaw.gateway.agent_client unavailable — skipping agent_client patch")
            agent_client_cls = None
    if agent_client_cls is not None:
        def factory_send(original):
            async def traced(self, envelope):
                attrs = _envelope_attrs(envelope)
                with tracer.start_as_current_span("jiuwenclaw.gateway.agent.request", kind=SpanKind.CLIENT, attributes=attrs):
                    _inject_traceparent(envelope)
                    return await original(self, envelope)
            return traced

        def factory_send_stream(original):
            async def traced(self, envelope):
                attrs = _envelope_attrs(envelope)
                with tracer.start_as_current_span("jiuwenclaw.gateway.agent.request", kind=SpanKind.CLIENT, attributes=attrs):
                    _inject_traceparent(envelope)
                    async for chunk in original(self, envelope):
                        yield chunk
            return traced

        patch_method(agent_client_cls, "send_request", factory_send)
        patch_method(agent_client_cls, "send_request_stream", factory_send_stream)
