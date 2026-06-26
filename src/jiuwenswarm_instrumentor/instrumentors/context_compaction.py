# src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py
from __future__ import annotations
import logging

from opentelemetry.trace import SpanKind, StatusCode

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.wrap import patch_method

logger = logging.getLogger("jiuwenswarm_instrumentor")


def _count(context, messages):
    """用引擎的 token_counter 计数。fail-soft → 0。"""
    try:
        return context.token_counter().count_messages(messages or [])
    except Exception:
        return 0


def _window_messages(window):
    """从 ContextWindow 取消息列表。适配 get_messages() / .messages / list。fail-soft → []。"""
    try:
        if window is None:
            return []
        if hasattr(window, "get_messages"):
            return window.get_messages() or []
        if hasattr(window, "messages"):
            return window.messages or []
        if isinstance(window, (list, tuple)):
            return list(window)
        return []
    except Exception:
        return []


def _sid(context):
    try:
        return str(context.session_id() or "")
    except Exception:
        return ""


def _cid(context):
    try:
        return str(context.context_id() or "")
    except Exception:
        return ""


def _emit(tracer, metrics, context, *, path, processor_type, before_tokens, after_tokens, before_msgs, after_msgs):
    """出 context.compaction span + 2 metric。只在 after<before 时调。fail-soft。"""
    try:
        saved = before_tokens - after_tokens
        attrs = {
            A.GEN_AI_CONTEXT_COMPACTION_PATH: path,
            A.GEN_AI_CONTEXT_COMPACTION_PROCESSOR_TYPE: processor_type,
            A.GEN_AI_CONTEXT_COMPACTION_TOKENS_BEFORE: before_tokens,
            A.GEN_AI_CONTEXT_COMPACTION_TOKENS_AFTER: after_tokens,
            A.GEN_AI_CONTEXT_COMPACTION_TOKENS_SAVED: saved,
            A.GEN_AI_CONTEXT_COMPACTION_MESSAGES_BEFORE: before_msgs,
            A.GEN_AI_CONTEXT_COMPACTION_MESSAGES_AFTER: after_msgs,
            A.JIUWENCLAW_SESSION_ID: _sid(context),
            A.JIUWENCLAW_CONTEXT_ID: _cid(context),
        }
        with tracer.start_as_current_span("context.compaction", kind=SpanKind.INTERNAL, attributes=attrs) as span:
            span.set_status(StatusCode.OK)
        mattrs = {A.GEN_AI_CONTEXT_COMPACTION_PATH: path,
                  A.GEN_AI_CONTEXT_COMPACTION_PROCESSOR_TYPE: processor_type,
                  A.GEN_AI_SYSTEM: "jiuwenclaw"}
        metrics.record_context_compaction(1, saved, mattrs)
    except Exception:
        logger.debug("[instrumentor] context compaction emit failed", exc_info=True)


def instrument_context_compaction(tracer, metrics, *, session_context_cls=None, processor_classes=None):
    """wrap SessionModelContext.add_messages (ADD 整体) + 3 个 GET processor 的 on_get_context_window
    (per-processor)。每次真压缩(after<before)出 context.compaction span + 2 metric。fail-soft per target。"""
    # --- ADD 整体 ---
    if session_context_cls is None:
        try:
            from openjiuwen.core.context_engine.context.context import SessionModelContext
            session_context_cls = SessionModelContext
        except Exception:
            session_context_cls = None
    if session_context_cls is not None:
        def factory_add(original):
            async def traced(self, *args, **kw):
                # ADD 整体:buffer 前后 delta = 纯压缩(ADD processor 链直接改 buffer)。
                # 注意:delta 会 net 掉新追加的 messages_to_add(它们在 processor 链后 add_back),
                # 所以 tokens_saved 是"整体"近似(spec §2),不是纯压缩 savings。
                bt = _count(self, self.get_messages())
                bm = len(self.get_messages() or [])
                result = await original(self, *args, **kw)  # IrreducibleContextError 透传
                at = _count(self, self.get_messages())
                am = len(self.get_messages() or [])
                if at < bt:
                    _emit(tracer, metrics, self, path="ADD", processor_type="",
                          before_tokens=bt, after_tokens=at, before_msgs=bm, after_msgs=am)
                return result
            return traced
        patch_method(session_context_cls, "add_messages", factory_add)

    # --- GET per-processor ---
    if processor_classes is None:
        processor_classes = []
        for _name, _path in (
            ("FullCompactProcessor", "openjiuwen.core.context_engine.processor.compressor.full_compact_processor"),
            ("RoundLevelCompressor", "openjiuwen.core.context_engine.processor.compressor.round_level_compressor"),
            ("ToolResultDedupProcessor", "openjiuwen.core.context_engine.processor.compressor.tool_result_dedup_processor"),
        ):
            try:
                _m = __import__(_path, fromlist=[_name])
                processor_classes.append(getattr(_m, _name))
            except Exception:
                pass
    for cls in processor_classes:
        def factory_get(original):
            async def traced(self, context, context_window, **kw):
                wmsgs = _window_messages(context_window)
                bt = _count(context, wmsgs)
                bm = len(wmsgs)
                event, context_window = await original(self, context, context_window, **kw)  # 透传异常
                wmsgs2 = _window_messages(context_window)
                at = _count(context, wmsgs2)
                am = len(wmsgs2)
                if at < bt:
                    ptype = ""
                    try:
                        ptype = str(self.processor_type() or "")
                    except Exception:
                        pass
                    _emit(tracer, metrics, context, path="GET", processor_type=ptype,
                          before_tokens=bt, after_tokens=at, before_msgs=bm, after_msgs=am)
                return event, context_window
            return traced
        patch_method(cls, "on_get_context_window", factory_get)
