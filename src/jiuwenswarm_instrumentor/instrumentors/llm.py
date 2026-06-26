# src/jiuwenswarm_instrumentor/instrumentors/llm.py
from __future__ import annotations
import json
import time
import types

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
from jiuwenswarm_instrumentor.instrumentors.context_tokens import record_context_composition
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def _resolve_provider(self) -> str:
    try:
        cp = self.model_client_config.client_provider
        return cp.value if hasattr(cp, "value") else str(cp)
    except Exception:
        return "unknown"


def _resolve_model(self, model_kwarg) -> str:
    return model_kwarg or getattr(getattr(self, "model_config", None), "model_name", None) or "unknown"


def _common_attrs(self, model, provider):
    attrs = {
        A.GEN_AI_SYSTEM: provider.lower(),
        A.GEN_AI_REQUEST_MODEL: model,
        A.GEN_AI_RESPONSE_MODEL: model,
        A.GEN_AI_OPERATION_NAME: "chat",
        A.GEN_AI_REQUEST_STREAMING: False,
    }
    attrs.update(current_request_attrs())
    temp = getattr(getattr(self, "model_config", None), "temperature", None)
    if temp is not None:
        attrs[A.GEN_AI_REQUEST_TEMPERATURE] = float(temp)
    return attrs


def _msg_role(msg):
    role = getattr(msg, "role", None)
    if role is None and isinstance(msg, dict):
        role = msg.get("role")
    return role or "unknown"


def _msg_content(msg):
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content", "")
    return str(content) if content is not None else ""


def _cap(text, max_len):
    text = "" if text is None else str(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _tc_field(tc, name):
    v = getattr(tc, name, None)
    if v is None and isinstance(tc, dict):
        v = tc.get(name)
    return v


def _record_input_messages(span, messages, max_len):
    try:
        entries = []
        for m in messages:
            entry = {"role": _msg_role(m),
                     "parts": [{"type": "text", "content": _cap(_msg_content(m), max_len)}]}
            if _msg_role(m) == "tool":
                tcid = getattr(m, "tool_call_id", "")
                if isinstance(m, dict):
                    tcid = m.get("tool_call_id", "")
                entry["tool_call_id"] = str(tcid)
            entries.append(entry)
        span.set_attribute(A.GEN_AI_INPUT_MESSAGES, json.dumps(entries, ensure_ascii=False))
    except Exception:
        pass


def _record_output_message(span, content, tool_calls, max_len):
    """Record assistant output. For tool-call responses (no text), record the tool_calls
    so the model's tool selection is visible."""
    try:
        entry = {"role": "assistant", "parts": [{"type": "text", "content": _cap(content, max_len)}]}
        if tool_calls:
            tcs = []
            for tc in tool_calls:
                tcs.append({
                    "id": str(_tc_field(tc, "id") or ""),
                    "name": str(_tc_field(tc, "name") or ""),
                    "arguments": _cap(_tc_field(tc, "arguments"), max_len),
                })
            entry["tool_calls"] = tcs
        span.set_attribute(A.GEN_AI_OUTPUT_MESSAGES, json.dumps([entry], ensure_ascii=False))
    except Exception:
        pass


def _record_tool_definitions(span, tools, max_len):
    try:
        if not tools:
            return
        defs = []
        for t in tools:
            name = _tc_field(t, "name")
            desc = _tc_field(t, "description")
            params = _tc_field(t, "parameters")
            defs.append({"name": str(name or ""),
                         "description": _cap(desc, max_len),
                         "parameters": params})
        span.set_attribute(A.GEN_AI_TOOL_DEFINITIONS, json.dumps(defs, ensure_ascii=False))
    except Exception:
        pass


def _record_usage(span, metrics, result, model, provider):
    usage = getattr(result, "usage_metadata", None)
    if usage is None:
        return
    base = {A.GEN_AI_REQUEST_MODEL: model, A.GEN_AI_SYSTEM: provider.lower()}
    base.update(current_request_attrs())
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or (inp + out)
    cache = getattr(usage, "cache_tokens", 0) or 0
    span.set_attribute(A.GEN_AI_USAGE_INPUT_TOKENS, inp)
    span.set_attribute(A.GEN_AI_USAGE_OUTPUT_TOKENS, out)
    span.set_attribute(A.GEN_AI_USAGE_TOTAL_TOKENS, total)
    if cache:
        span.set_attribute(A.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, cache)
    metrics.record_token_usage(inp, out, base)


_MEMORY_OP_PROMPTS = (
    ("You are a session memory updater", "session_memory_update"),
    ("Your task is to create a detailed summary", "full_compact_summary"),
)


def _detect_memory_operation(messages):
    """Sniff system prompt prefix → return operation name or None. Fail-soft."""
    try:
        for msg in messages or []:
            if _msg_role(msg) == "system":
                content = _msg_content(msg)
                for prefix, op_name in _MEMORY_OP_PROMPTS:
                    if content.startswith(prefix):
                        return op_name
                break  # only first system message
    except Exception:
        pass
    return None


def instrument_llm(tracer, metrics, *, log_messages=False, message_max_length=4096, model_client_cls=None):
    """Wrap OpenAIModelClient.invoke + .stream (openjiuwen 0.1.10)."""
    if model_client_cls is None:
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
        model_client_cls = OpenAIModelClient

    def invoke_factory(original):
        async def traced_invoke(self, messages, *, tools=None, temperature=None, top_p=None,
                                model=None, max_tokens=None, stop=None, output_parser=None,
                                timeout=None, **kw):
            provider = _resolve_provider(self)
            mdl = _resolve_model(self, model)
            attrs = _common_attrs(self, mdl, provider)
            _op = _detect_memory_operation(messages)
            if _op:
                attrs[A.GEN_AI_OPERATION_NAME] = _op
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT, attributes=attrs) as span:
                if log_messages:
                    _record_input_messages(span, messages, message_max_length)
                    _record_tool_definitions(span, tools, message_max_length)
                try:
                    result = await original(self, messages, tools=tools, temperature=temperature,
                                           top_p=top_p, model=model, max_tokens=max_tokens, stop=stop,
                                           output_parser=output_parser, timeout=timeout, **kw)
                    _record_usage(span, metrics, result, mdl, provider)
                    finish = getattr(result, "finish_reason", None)
                    if finish and str(finish) != "null":
                        span.set_attribute(A.GEN_AI_RESPONSE_FINISH_REASON, str(finish))
                    if log_messages:
                        _record_output_message(span, _msg_content(result),
                                               getattr(result, "tool_calls", None), message_max_length)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    metrics.record_llm_duration(time.monotonic() - start,
                                                {A.GEN_AI_REQUEST_MODEL: mdl, A.GEN_AI_SYSTEM: provider.lower()})
                    record_context_composition(span, metrics, messages, tools, mdl)
        return traced_invoke

    def stream_factory(original):
        async def traced_stream(self, messages, *, tools=None, temperature=None, top_p=None,
                               model=None, max_tokens=None, stop=None, output_parser=None,
                               timeout=None, **kw):
            provider = _resolve_provider(self)
            mdl = _resolve_model(self, model)
            attrs = _common_attrs(self, mdl, provider)
            attrs[A.GEN_AI_REQUEST_STREAMING] = True
            _op = _detect_memory_operation(messages)
            if _op:
                attrs[A.GEN_AI_OPERATION_NAME] = _op
            start = time.monotonic()
            # NOT start_as_current_span: we don't keep this span current during the async
            # iteration, so the caller's (agent's) mid-stream tool execution does NOT nest
            # under gen_ai.chat. Per OTel GenAI convention, gen_ai.chat (model inference)
            # and gen_ai.tool (tool execution) are siblings under the agent span.
            span = tracer.start_span("gen_ai.chat", kind=SpanKind.CLIENT, attributes=attrs)
            if log_messages:
                _record_input_messages(span, messages, message_max_length)
                _record_tool_definitions(span, tools, message_max_length)
            first = True
            final_usage = None
            finish = None
            output_parts = []
            tool_call_acc = {}  # index -> {id, name, arguments} assembled from deltas
            try:
                async for chunk in original(self, messages, tools=tools, temperature=temperature,
                                            top_p=top_p, model=model, max_tokens=max_tokens, stop=stop,
                                            output_parser=output_parser, timeout=timeout, **kw):
                    if first:
                        first = False
                        span.set_attribute(A.GEN_AI_STREAMING_FIRST_TOKEN_MS, (time.monotonic() - start) * 1000)
                    u = getattr(chunk, "usage_metadata", None)
                    if u is not None:
                        final_usage = u
                    fr = getattr(chunk, "finish_reason", None)
                    if fr and str(fr) != "null":
                        finish = fr
                    if log_messages:
                        c = _msg_content(chunk)
                        if c:
                            output_parts.append(c)
                        tcs = getattr(chunk, "tool_calls", None)
                        if tcs:
                            for tc in tcs:
                                idx = _tc_field(tc, "index")
                                idx = idx if idx is not None else 0
                                acc = tool_call_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                                if _tc_field(tc, "id"):
                                    acc["id"] = str(_tc_field(tc, "id"))
                                if _tc_field(tc, "name"):
                                    acc["name"] += str(_tc_field(tc, "name"))
                                if _tc_field(tc, "arguments"):
                                    acc["arguments"] += str(_tc_field(tc, "arguments"))
                    yield chunk
                if final_usage is not None:
                    _record_usage(span, metrics,
                                 types.SimpleNamespace(usage_metadata=final_usage), mdl, provider)
                if finish:
                    span.set_attribute(A.GEN_AI_RESPONSE_FINISH_REASON, finish)
                if log_messages:
                    if output_parts:
                        _record_output_message(span, "".join(output_parts), None, message_max_length)
                    elif tool_call_acc:
                        assembled = [tool_call_acc[k] for k in sorted(tool_call_acc)]
                        _record_output_message(span, "", assembled, message_max_length)
                span.set_status(StatusCode.OK)
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc)[:256])
                span.record_exception(exc)
                raise
            finally:
                metrics.record_llm_duration(time.monotonic() - start,
                                            {A.GEN_AI_REQUEST_MODEL: mdl, A.GEN_AI_SYSTEM: provider.lower()})
                record_context_composition(span, metrics, messages, tools, mdl)
                span.end()
        return traced_stream

    patch_method(model_client_cls, "invoke", invoke_factory)
    patch_method(model_client_cls, "stream", stream_factory)
