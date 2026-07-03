# src/jiuwenswarm_instrumentor/instrumentors/agent.py
from __future__ import annotations
import json
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


def _cap(text, max_len):
    text = "" if text is None else str(text)
    if not max_len or max_len <= 0:  # 0 / none / off → no truncation
        return text
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _extract_user_input_text(inputs):
    """Best-effort extract this turn's user input text from ReActAgent.invoke `inputs`.

    `inputs` varies by caller: a bare string, a dict (e.g. {"query": ...}), or a
    list of chat messages. We prefer an explicit `user`-role message, then common
    dict keys, then a plain string, finally str() as a fallback. Fail-soft → "".
    """
    try:
        if inputs is None:
            return ""
        if isinstance(inputs, str):
            return inputs
        if isinstance(inputs, dict):
            for k in ("content", "query", "message", "text", "input", "prompt"):
                v = inputs.get(k)
                if isinstance(v, str) and v:
                    return v
            msgs = inputs.get("messages")
            if isinstance(msgs, list):
                return _extract_user_input_text(msgs)
            return str(inputs)
        if isinstance(inputs, (list, tuple)):
            parts = []
            for m in inputs:
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                if role == "user":
                    c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                    if c:
                        parts.append(str(c))
            if parts:
                return "\n".join(parts)
            return str(inputs)
        return str(inputs)
    except Exception:
        return ""


def _record_user_input(span, inputs, max_len):
    """Record the user's input for this turn on the agent.invoke span as a standard
    OTel GenAI ``gen_ai.input.messages`` payload (single user message), so a user
    can identify which message they sent from the trace's root span. Phoenix and
    Langfuse both recognize this attribute and render it as the span's input."""
    try:
        text = _extract_user_input_text(inputs)
        if text:
            entry = {"role": "user",
                     "parts": [{"type": "text", "content": _cap(text, max_len)}]}
            span.set_attribute(A.GEN_AI_INPUT_MESSAGES,
                               json.dumps([entry], ensure_ascii=False))
    except Exception:
        pass


def instrument_agent(tracer, metrics, *, agent_cls=None, log_messages=False, message_max_length=4096):
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
                    if log_messages:
                        _record_user_input(span, inputs, message_max_length)
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
