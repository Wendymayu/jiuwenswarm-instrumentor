# src/jiuwenswarm_instrumentor/instrumentors/llm.py
from __future__ import annotations
import time
import types

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
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


def instrument_llm(tracer, metrics, *, log_messages=False, model_client_cls=None):
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
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT, attributes=attrs) as span:
                try:
                    result = await original(self, messages, tools=tools, temperature=temperature,
                                           top_p=top_p, model=model, max_tokens=max_tokens, stop=stop,
                                           output_parser=output_parser, timeout=timeout, **kw)
                    _record_usage(span, metrics, result, mdl, provider)
                    finish = getattr(result, "finish_reason", None)
                    if finish and str(finish) != "null":
                        span.set_attribute(A.GEN_AI_RESPONSE_FINISH_REASON, str(finish))
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    metrics.record_llm_duration(time.monotonic() - start,
                                                {A.GEN_AI_REQUEST_MODEL: mdl, A.GEN_AI_SYSTEM: provider.lower()})
        return traced_invoke

    def stream_factory(original):
        async def traced_stream(self, messages, *, tools=None, temperature=None, top_p=None,
                               model=None, max_tokens=None, stop=None, output_parser=None,
                               timeout=None, **kw):
            provider = _resolve_provider(self)
            mdl = _resolve_model(self, model)
            attrs = _common_attrs(self, mdl, provider)
            attrs[A.GEN_AI_REQUEST_STREAMING] = True
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT, attributes=attrs) as span:
                first = True
                final_usage = None
                finish = None
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
                        yield chunk
                    if final_usage is not None:
                        _record_usage(span, metrics,
                                     types.SimpleNamespace(usage_metadata=final_usage), mdl, provider)
                    if finish:
                        span.set_attribute(A.GEN_AI_RESPONSE_FINISH_REASON, finish)
                    span.set_status(StatusCode.OK)
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    metrics.record_llm_duration(time.monotonic() - start,
                                                {A.GEN_AI_REQUEST_MODEL: mdl, A.GEN_AI_SYSTEM: provider.lower()})
        return traced_stream

    patch_method(model_client_cls, "invoke", invoke_factory)
    patch_method(model_client_cls, "stream", stream_factory)
