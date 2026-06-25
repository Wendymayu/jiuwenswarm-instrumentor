# src/jiuwenswarm_instrumentor/instrumentors/logs.py
from __future__ import annotations
import logging
import time
import traceback

from opentelemetry._logs import LogRecord, SeverityNumber, get_logger
from opentelemetry.context import get_current

logger = logging.getLogger("jiuwenswarm_instrumentor")

# stdlib LogRecord 内置属性,不作为 OTel attribute 输出
# https://docs.python.org/3/library/logging.html#logrecord-attributes
_RESERVED_ATTRS = frozenset({
    "asctime", "args", "created", "exc_info", "exc_text", "filename",
    "funcName", "getMessage", "message", "levelname", "levelno", "lineno",
    "module", "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})

_SEVERITY_BY_LEVELNO = {
    0: SeverityNumber.INFO,
    10: SeverityNumber.DEBUG,
    20: SeverityNumber.INFO,
    30: SeverityNumber.WARN,
    40: SeverityNumber.ERROR,
    50: SeverityNumber.FATAL,
}
_SEVERITY_TEXT = {0: "INFO", 10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "FATAL"}

_EVENT_KEYS = ("event_name", "event.name", "event")


def _severity_number(levelno):
    return _SEVERITY_BY_LEVELNO.get(levelno, SeverityNumber.UNSPECIFIED)


def _severity_text(levelno):
    return _SEVERITY_TEXT.get(levelno, "UNSPECIFIED")


def _level_to_stdlib(level):
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def _cap(text, max_len):
    text = "" if text is None else str(text)
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _format_body(record):
    body = record.getMessage()
    if record.exc_info:
        body = body + "\n" + "".join(traceback.format_exception(*record.exc_info))
    return body


def _extract_event_name(record):
    for key in _EVENT_KEYS:
        val = getattr(record, key, None)
        if val:
            return str(val)
    return None


def _record_to_attributes(record, max_len):
    attrs = {}
    event_name = _extract_event_name(record)
    for k, v in vars(record).items():
        if k in _RESERVED_ATTRS:
            continue
        if event_name and k in _EVENT_KEYS:
            continue  # promoted to event.name attribute / native field
        if isinstance(v, str):
            attrs[k] = _cap(v, max_len)
        elif isinstance(v, (bool, int, float)):
            attrs[k] = v
        else:
            attrs[k] = _cap(str(v), max_len)
    attrs["code.filepath"] = _cap(record.pathname, max_len)
    attrs["code.function"] = record.funcName
    attrs["code.lineno"] = record.lineno
    attrs["log.logger"] = record.name
    attrs["thread.id"] = record.thread
    attrs["process.id"] = record.process
    return attrs


class OTelLogHandler(logging.Handler):
    """Bridge stdlib logging records -> OTel LogRecords (fail-soft).

    Attached directly to logging.getLogger('jiuwenclaw') because that logger
    has propagate=False. Trace correlation is automatic via context=get_current().
    """

    def __init__(self, otel_logger, *, level="INFO", excluded_loggers=(), message_max_length=8192):
        super().__init__(level=_level_to_stdlib(level))
        self._otel_logger = otel_logger
        self._excluded = tuple(excluded_loggers)
        self._max_len = message_max_length

    def emit(self, record):
        try:
            if any(s in record.name for s in self._excluded):
                return
            attrs = _record_to_attributes(record, self._max_len)
            event_name = _extract_event_name(record)
            if event_name:
                # labubu 的 OTLP proto (v0.20.0) 从 attributes["event.name"] 提取 event_name;
                # 同时设原生 event_name 字段,兼容 Phoenix 等新协议后端。
                attrs["event.name"] = event_name
            kwargs = dict(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=get_current(),
                severity_text=_severity_text(record.levelno),
                severity_number=_severity_number(record.levelno),
                body=_cap(_format_body(record), self._max_len),
                attributes=attrs,
            )
            if event_name:
                kwargs["event_name"] = event_name
            self._otel_logger.emit(LogRecord(**kwargs))
        except Exception:
            self.handleError(record)


def _copy_filters_from(src_logger, dst_handler):
    """Copy logging.Filter objects from src_logger's existing handlers onto dst_handler.
    Returns count copied (used to decide redaction/level fallback). Fail-soft."""
    count = 0
    seen = set()
    for h in src_logger.handlers:
        for f in getattr(h, "filters", ()):
            key = id(f)
            if key in seen:
                continue
            seen.add(key)
            try:
                dst_handler.addFilter(f)
                count += 1
            except Exception:
                pass
    return count


def _patch_setup_logger_to_reattach(attach):
    """Wrap jiuwenclaw.utils.setup_logger so our handler is re-attached after it
    clears handlers. Fail-soft: no-op if jiuwenclaw.utils isn't importable."""
    try:
        import jiuwenclaw.utils as _u  # type: ignore
    except Exception:
        return
    original = getattr(_u, "setup_logger", None)
    if original is None or getattr(original, "_jiuwenswarm_wrapped", False):
        return

    def wrapped(*a, **kw):
        try:
            return original(*a, **kw)
        finally:
            try:
                attach()
            except Exception:
                logger.debug("[instrumentor] logs re-attach after setup_logger failed", exc_info=True)

    wrapped._jiuwenswarm_wrapped = True
    _u.setup_logger = wrapped


def instrument_logs(otel_logger=None, *, level="INFO", excluded_loggers=(), message_max_length=8192):
    """Attach an OTelLogHandler to logging.getLogger('jiuwenclaw').
    Idempotent + fail-soft. Re-attaches after jiuwenclaw's setup_logger clears handlers.
    Copies the app's existing logging.Filters (redaction) onto our handler; falls back
    to WARNING-only if no filters are found (no redaction guarantee)."""
    if otel_logger is None:
        otel_logger = get_logger("jiuwenswarm_instrumentor.logs")

    handler = OTelLogHandler(
        otel_logger, level=level,
        excluded_loggers=excluded_loggers,
        message_max_length=message_max_length,
    )
    handler._jiuwenswarm_otel = True  # idempotency marker

    def attach():
        jl = logging.getLogger("jiuwenclaw")
        if any(getattr(h, "_jiuwenswarm_otel", False) for h in jl.handlers):
            return  # already attached
        copied = _copy_filters_from(jl, handler)
        if copied:
            handler.setLevel(_level_to_stdlib(level))
        else:
            handler.setLevel(max(_level_to_stdlib(level), logging.WARNING))
        jl.addHandler(handler)

    attach()
    _patch_setup_logger_to_reattach(attach)
