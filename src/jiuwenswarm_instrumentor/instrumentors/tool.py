# src/jiuwenswarm_instrumentor/instrumentors/tool.py
from __future__ import annotations
import hashlib
import json
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def _cap(text, max_len):
    text = "" if text is None else str(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _parse_args(arguments):
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _skill_id(skill_name):
    return "skill_" + hashlib.sha1(skill_name.encode("utf-8")).hexdigest()[:8]


def _session_id(session):
    try:
        return session.get_session_id() if session is not None else None
    except Exception:
        return None


def _skill_metric_attrs(skill_name):
    base = {A.GEN_AI_SKILL_NAME: skill_name, A.GEN_AI_SYSTEM: "jiuwenclaw"}
    base.update(current_request_attrs())
    return base


def _enrich_skill_load(span, tool_call, tool_msg, session, metrics, is_error):
    """OTel GenAI #86: set skill attrs + skill.loaded event on the gen_ai.tool span."""
    try:
        skill_name = ""
        skill_path = ""
        if tool_msg is not None:
            meta = getattr(tool_msg, "metadata", None) or {}
            if isinstance(meta, dict) and (meta.get("is_skill_body") or meta.get("original_is_skill_body")):
                skill_name = str(meta.get("skill_name", "") or "")
                skill_path = str(meta.get("relative_file_path", "") or "")
        if not skill_name:
            args = _parse_args(getattr(tool_call, "arguments", ""))
            skill_name = str(args.get("skill_name", "") or "") if isinstance(args, dict) else ""
        if not skill_name:
            return
        span.set_attribute(A.GEN_AI_OPERATION_NAME, "load_skill")
        span.set_attribute(A.GEN_AI_SKILL_NAME, skill_name)
        span.set_attribute(A.GEN_AI_SKILL_ID, _skill_id(skill_name))
        span.add_event("skill.loaded", {"skill.name": skill_name, "skill.path": skill_path})
        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
        sid = _session_id(session) or ""
        skill_state.record_load(sid, skill_name)
        attrs = _skill_metric_attrs(skill_name)
        metrics.record_skill_call(attrs)
        if is_error:
            metrics.record_skill_error(attrs)
    except Exception:
        pass


def _enrich_skill_release(span, tool_call, session, metrics, is_error):
    try:
        args = _parse_args(getattr(tool_call, "arguments", ""))
        skill_name = str(args.get("skill_name", "") or "") if isinstance(args, dict) else ""
        if not skill_name:
            return
        span.set_attribute(A.GEN_AI_OPERATION_NAME, "release_skill")
        span.set_attribute(A.GEN_AI_SKILL_NAME, skill_name)
        span.add_event("skill.released", {"skill.name": skill_name})
        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
        sid = _session_id(session) or ""
        start = skill_state.pop_release(sid, skill_name)
        attrs = _skill_metric_attrs(skill_name)
        if start is not None:
            metrics.record_skill_duration(time.monotonic() - start, attrs)
        if is_error:
            metrics.record_skill_error(attrs)
    except Exception:
        pass


def instrument_tool(tracer, metrics, *, log_messages=False, message_max_length=4096, ability_cls=None):
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
            if log_messages:
                attrs[A.GEN_AI_TOOL_ARGUMENTS] = _cap(getattr(tool_call, "arguments", ""), message_max_length)
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
                    if log_messages and tool_msg is not None:
                        span.set_attribute(A.GEN_AI_TOOL_RESULT, _cap(getattr(tool_msg, "content", ""), message_max_length))
                    # --- skill enrichment (OTel GenAI #86) ---
                    if name == "skill_tool":
                        _enrich_skill_load(span, tool_call, tool_msg, session, metrics, is_error)
                    elif name == "skill_complete":
                        _enrich_skill_release(span, tool_call, session, metrics, is_error)
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
