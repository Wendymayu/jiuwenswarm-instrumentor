# src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py
from __future__ import annotations
import json
import logging

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs

logger = logging.getLogger("jiuwenswarm_instrumentor")


# --- token counters (improvement #1: per-model tokenizer) ---

class _TiktokenCounter:
    """tiktoken-based counter. count() falls back to len//4 on encode error."""
    def __init__(self, enc):
        self._enc = enc
    def count(self, text):
        try:
            return len(self._enc.encode(text or "", disallowed_special=()))
        except Exception:
            return len(text or "") // 4


class _LenCounter:
    """Ultimate fallback: len(text)//4."""
    def count(self, text):
        return len(text or "") // 4


_ENC_CACHE = {}


def _get_token_counter(model_name):
    """Resolve tokenizer by model name (improvement #1) → cl100k_base → len//4.
    Encoding objects are cached (creation is expensive)."""
    try:
        import tiktoken
    except Exception:
        return _LenCounter()
    enc = None
    if model_name:
        try:
            enc = _ENC_CACHE.get(("for", model_name))
            if enc is None:
                enc = tiktoken.encoding_for_model(model_name)
                _ENC_CACHE[("for", model_name)] = enc
        except Exception:
            enc = None
    if enc is None:
        try:
            enc = _ENC_CACHE.get(("base", "cl100k_base"))
            if enc is None:
                enc = tiktoken.get_encoding("cl100k_base")
                _ENC_CACHE[("base", "cl100k_base")] = enc
        except Exception:
            return _LenCounter()
    return _TiktokenCounter(enc)


# --- message helpers (mirror telemetry_rail.py:1095-1145) ---

def _extract_text_content(msg):
    """str → str; list (multimodal) → join text parts only (images/URLs excluded)."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif hasattr(part, "type") and getattr(part, "type", "") == "text":
                parts.append(getattr(part, "text", ""))
        return "\n".join(parts)
    return str(content)


def _count_assistant_extras(msg, counter):
    """tool_calls (JSON) + reasoning_content tokens. 0 if no extras."""
    extra = 0
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls is None and isinstance(msg, dict):
        tool_calls = msg.get("tool_calls")
    if tool_calls:
        if hasattr(msg, "model_dump"):
            tc_json = msg.model_dump().get("tool_calls", tool_calls)
        else:
            tc_json = tool_calls
        extra += counter.count(json.dumps(tc_json, ensure_ascii=False))
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning is None and isinstance(msg, dict):
        reasoning = msg.get("reasoning_content")
    if reasoning and isinstance(reasoning, str):
        extra += counter.count(reasoning)
    return extra


# --- tool-definition helpers (mirror telemetry_rail.py:1326-1436) ---

def _serialize_tool_def(tool, idx):
    """Return (name, framed_piece) where piece = <|start|>functions.{name}:{idx}\n{json}<|end|>."""
    if isinstance(tool, dict):
        func_obj = tool.get("function", tool)
        name = func_obj.get("name", "") if isinstance(func_obj, dict) else ""
        json_str = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
        return name, f"<|start|>functions.{name}:{idx}\n{json_str}<|end|>"
    name = getattr(tool, "name", "") or ""
    func = getattr(tool, "function", None)
    if not name and func:
        name = getattr(func, "name", "")
    func_obj = {"name": name or ""}
    desc = getattr(tool, "description", "")
    if not desc and func:
        desc = getattr(func, "description", "")
    func_obj["description"] = desc or ""
    parameters = getattr(tool, "parameters", None)
    if parameters is None and func:
        parameters = getattr(func, "parameters", None)
    if parameters is not None:
        if isinstance(parameters, type) and hasattr(parameters, "model_json_schema"):
            parameters = parameters.model_json_schema()
        func_obj["parameters"] = parameters
    tool_def = {"type": getattr(tool, "type", "function"), "function": func_obj}
    json_str = json.dumps(tool_def, ensure_ascii=False, separators=(",", ":"))
    return name or "", f"<|start|>functions.{func_obj['name']}:{idx}\n{json_str}<|end|>"


def _count_tool_definitions(tools, counter):
    """Return {total, per_tool[name]} using the framed format."""
    per_tool = {}
    if not tools:
        return {"total": 0, "per_tool": per_tool}
    for idx, tool in enumerate(tools):
        name, piece = _serialize_tool_def(tool, idx)
        name = name or f"_unknown_{idx}"
        per_tool[name] = per_tool.get(name, 0) + counter.count(piece)
    return {"total": sum(per_tool.values()), "per_tool": per_tool}


# --- main entry ---

def record_context_composition(span, metrics, messages, tools, model_name, *, counter=None):
    """Record 6 gen_ai.context.* span attrs + gen_ai.usage.estimated + 2 metrics.
    Fail-soft: any failure → no attrs, no raise. Sync (call after LLM returns, before span.end)."""
    try:
        counter = counter or _get_token_counter(model_name)
        tokens = {"skill": 0, "system": 0, "user": 0, "assistant": 0, "tool": 0}
        per_skill = {}  # skill_name -> running token total
        for msg in messages:
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
            role = role or "unknown"
            est = counter.count(_extract_text_content(msg))
            metadata = getattr(msg, "metadata", None) or {}
            if isinstance(msg, dict):
                metadata = msg.get("metadata", {}) or {}
            is_skill = (
                (role == "tool" and (metadata.get("is_skill_body") or metadata.get("original_is_skill_body")))
                or (role == "system" and metadata.get("active_skill_pin"))
            )
            if is_skill:
                tokens["skill"] += est
                sname = str(metadata.get("skill_name", "") or "")
                if sname and sname.lower() not in ("true", "false"):
                    per_skill[sname] = per_skill.get(sname, 0) + est
            elif role == "tool":
                tokens["tool"] += est
            elif role == "system":
                tokens["system"] += est
            elif role == "assistant":
                tokens["assistant"] += est + _count_assistant_extras(msg, counter)
            elif role == "user":
                tokens["user"] += est
        td = _count_tool_definitions(tools, counter)
        span.set_attribute(A.GEN_AI_CONTEXT_SKILL, tokens["skill"])
        span.set_attribute(A.GEN_AI_CONTEXT_SYSTEM_PROMPT, tokens["system"])
        span.set_attribute(A.GEN_AI_CONTEXT_USER_MESSAGES, tokens["user"])
        span.set_attribute(A.GEN_AI_CONTEXT_ASSISTANT_MESSAGES, tokens["assistant"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_RESULTS, tokens["tool"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_DEFINITIONS, td["total"])
        span.set_attribute(A.GEN_AI_USAGE_ESTIMATED, True)
        base = {A.GEN_AI_SYSTEM: "jiuwenclaw"}
        base.update(current_request_attrs())
        for sname, t in per_skill.items():
            metrics.record_skill_token_usage(t, {**base, A.GEN_AI_SKILL_NAME: sname})
        for tname, t in td["per_tool"].items():
            metrics.record_tool_token_usage(t, {**base, A.GEN_AI_TOOL_NAME: tname})
    except Exception:
        logger.debug("[instrumentor] context composition failed", exc_info=True)
