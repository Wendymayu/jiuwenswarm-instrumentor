# 日志采集 (Log Collection) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 jiuwenswarm-instrumentor 增加第三信号 logs —— 把 jiuwenclaw 的 stdlib `logging` 桥接到 OTel logs,经 OTLP 导出到 labubu,并与 trace 关联。

**Architecture:** 手写 `OTelLogHandler(logging.Handler)`,在 `emit()` 里把 stdlib `LogRecord` 翻译成 OTel `LogRecord`(`context=get_current()` 自动带 trace_id/span_id),直接挂到 `logging.getLogger("jiuwenclaw")`(因其 `propagate=False`)。provider 层用 `LoggerProvider` + `BatchLogRecordProcessor` + OTLPLogExporter,设全局 `opentelemetry._logs` proxy;instrumentor 层用 proxy 拿 logger。fail-soft 全程。零新依赖(OTel 1.43.0 已含 `sdk._logs` + OTLP `_log_exporter`)。

**Tech Stack:** Python 3.13(用 `py -3.13`)、opentelemetry-sdk 1.43.0、opentelemetry-exporter-otlp-proto-{http,grpc} 1.43.0、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-25-log-collection-design.md`

**关键 API 备注(实测 OTel 1.43.0):**
- `LogRecord` 构造全 keyword-only:`timestamp`/`observed_timestamp`/`context`/`severity_text`/`severity_number`/`body`/`attributes`/`event_name`/`exception`。**trace 关联靠 `context=get_current()`**(SDK 据此设 trace_id/span_id;直接传 `trace_id=`/`span_id=` 已 deprecated)。
- 导出器路径是 `_log_exporter`(单数下划线,不同于 traces 的 `trace_exporter`):`from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter`;HTTP endpoint `{endpoint}/v1/logs`。
- `InMemoryLogRecordExporter().get_finished_logs()` 返回 `tuple[ReadableLogRecord]`,每项 `.log_record.{severity_text,severity_number,body,attributes,trace_id,event_name}`。
- `SeverityNumber` 从 `opentelemetry._logs` 导入(API 层,public)。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/jiuwenswarm_instrumentor/config.py` | `InstrumentorConfig` 加 `logs_*` 字段 + `load_config()` 读 `OTEL_LOGS_*` | Modify |
| `src/jiuwenswarm_instrumentor/provider.py` | `init_providers` 加 logs 分支(`LoggerProvider` + processor + exporter)+ `_otlp_log_exporter` | Modify |
| `src/jiuwenswarm_instrumentor/instrumentors/logs.py` | `OTelLogHandler` + 映射 helper + `instrument_logs`(挂载/filter 复用/setup_logger patch) | Create |
| `src/jiuwenswarm_instrumentor/instrumentors/__init__.py` | `apply_instrumentors` 加条件 logs 步 | Modify |
| `src/jiuwenswarm_instrumentor/activate.py` | 启动日志加 `logs=%s` | Modify |
| `tests/test_config.py` | logs config 用例 | Modify |
| `tests/test_provider.py` | logs provider 用例 | Modify |
| `tests/instrumentors/test_logs.py` | handler + instrument_logs 共 10 用例 | Create |
| `tests/instrumentors/test_apply.py` | logs 被调用用例 | Modify |
| `docs/guides/start-jiuwenswarm-with-instrumentor.md` | 启动指南补 logs env + 验证 | Modify |

---

### Task 1: config — logs 字段 + 环境读取

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/test_config.py` 末尾追加:

```python
def test_logs_config_defaults():
    cfg = load_config()
    assert cfg.logs_exporter == "none"
    assert cfg.logs_protocol == "grpc"
    assert cfg.log_level == "INFO"
    assert cfg.log_excluded_loggers == ("jiuwenclaw.interface.resp",)
    assert cfg.log_message_max_length == 8192


def test_logs_env_overrides():
    os.environ["OTEL_LOGS_EXPORTER"] = "otlp"
    os.environ["OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"] = "http"
    os.environ["OTEL_LOGS_LEVEL"] = "debug"
    os.environ["OTEL_LOGS_EXCLUDED_LOGGERS"] = "a,b"
    os.environ["OTEL_LOG_MESSAGE_MAX_LENGTH"] = "100"
    try:
        cfg = load_config()
    finally:
        for k in ("OTEL_LOGS_EXPORTER", "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
                  "OTEL_LOGS_LEVEL", "OTEL_LOGS_EXCLUDED_LOGGERS",
                  "OTEL_LOG_MESSAGE_MAX_LENGTH"):
            del os.environ[k]
    assert cfg.logs_exporter == "otlp"
    assert cfg.logs_protocol == "http"
    assert cfg.log_level == "DEBUG"
    assert cfg.log_excluded_loggers == ("a", "b")
    assert cfg.log_message_max_length == 100
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/test_config.py::test_logs_config_defaults -v`
Expected: FAIL — `AttributeError: 'InstrumentorConfig' object has no attribute 'logs_exporter'`

- [ ] **Step 3: 实现** — 在 `config.py` 的 `InstrumentorConfig` dataclass 里,`message_max_length` 之后加字段:

```python
    logs_exporter: str = "none"          # otlp | console | none
    logs_endpoint: str = "http://localhost:4317"
    logs_protocol: str = "grpc"          # grpc | http
    logs_headers: dict = None
    log_level: str = "INFO"              # NOTSET|DEBUG|INFO|WARNING|ERROR|CRITICAL
    log_excluded_loggers: tuple = ()
    log_message_max_length: int = 8192
```

在 `load_config()` 的 `return InstrumentorConfig(...)` 里,`message_max_length=...` 之后加:

```python
        logs_exporter=_lower("OTEL_LOGS_EXPORTER", "none"),
        logs_endpoint=_str("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", endpoint),
        logs_protocol=_lower("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", protocol),
        logs_headers={**base_headers, **_headers("OTEL_EXPORTER_OTLP_LOGS_HEADERS")},
        log_level=(_str("OTEL_LOGS_LEVEL", "") or _str("OTEL_LOG_LEVEL", "INFO")).upper() or "INFO",
        log_excluded_loggers=tuple(
            s.strip() for s in _str("OTEL_LOGS_EXCLUDED_LOGGERS", "jiuwenclaw.interface.resp").split(",") if s.strip()
        ),
        log_message_max_length=int(_str("OTEL_LOG_MESSAGE_MAX_LENGTH", "8192") or 8192),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/test_config.py -v`
Expected: PASS(全部,含新 2 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/config.py tests/test_config.py
git commit -m "feat: logs config fields (OTEL_LOGS_* env)"
```

---

### Task 2: provider — logs 分支 + OTLP log exporter

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/provider.py`
- Test: `tests/test_provider.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/test_provider.py` 末尾追加:

```python
def test_logs_console_sets_logger_provider(monkeypatch):
    monkeypatch.setattr(provider.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(provider.metrics, "set_meter_provider", lambda p: None)
    captured = []
    monkeypatch.setattr(provider.logs, "set_logger_provider", lambda p: captured.append(p))
    cfg = InstrumentorConfig(enabled=True, logs_exporter="console", service_name="jc")
    tp, mp = provider.init_providers(cfg)
    assert tp is not None and mp is not None
    assert len(captured) == 1
    from opentelemetry.sdk._logs import LoggerProvider
    assert isinstance(captured[0], LoggerProvider)
    assert captured[0].resource.attributes.get("service.name") == "jc"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/test_provider.py::test_logs_console_sets_logger_provider -v`
Expected: FAIL — `AttributeError: module 'jiuwenswarm_instrumentor.provider' has no attribute 'logs'`

- [ ] **Step 3: 实现** — 在 `provider.py` 顶部 import 区加:

```python
import opentelemetry._logs as logs
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor, SimpleLogRecordProcessor, ConsoleLogExporter,
)
```

在 `init_providers` 的 `_attach_traces(tp, cfg)` 之后、`trace.set_tracer_provider(tp)` 之前加一行:

```python
        _attach_logs(resource, cfg)
```

在文件末尾加两个函数:

```python
def _attach_logs(resource, cfg):
    if getattr(cfg, "logs_exporter", "none") == "none":
        return
    try:
        lp = LoggerProvider(resource=resource)
        if cfg.logs_exporter == "otlp":
            lp.add_log_record_processor(BatchLogRecordProcessor(_otlp_log_exporter(cfg)))
        elif cfg.logs_exporter == "console":
            lp.add_log_record_processor(SimpleLogRecordProcessor(ConsoleLogExporter()))
        try:
            logs.set_logger_provider(lp)
        except Exception:
            pass  # already set in-process
    except Exception:
        logger.debug("[instrumentor] logs provider init failed", exc_info=True)


def _otlp_log_exporter(cfg):
    if cfg.logs_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        return OTLPLogExporter(endpoint=f"{cfg.logs_endpoint}/v1/logs", headers=cfg.logs_headers)
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    return OTLPLogExporter(endpoint=cfg.logs_endpoint, headers=cfg.logs_headers)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/test_provider.py -v`
Expected: PASS(全部,含新 1 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/provider.py tests/test_provider.py
git commit -m "feat: logs provider + OTLP log exporter in init_providers"
```

---

### Task 3: OTelLogHandler — severity/body/attributes/trace/event/excluded/fail-soft

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/logs.py`
- Test: `tests/instrumentors/test_logs.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/instrumentors/test_logs.py`:

```python
# tests/instrumentors/test_logs.py
import logging

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter, SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider

from jiuwenswarm_instrumentor.instrumentors.logs import OTelLogHandler


@pytest.fixture
def otel_logger():
    exporter = InMemoryLogRecordExporter()
    lp = LoggerProvider()
    lp.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    yield lp.get_logger("test"), exporter


@pytest.fixture
def clean_jiuwenclaw_logger():
    jl = logging.getLogger("jiuwenclaw")
    saved = (jl.handlers[:], jl.level, jl.propagate)
    jl.handlers = []
    jl.setLevel(logging.INFO)
    jl.propagate = False
    yield jl
    jl.handlers = saved[0]
    jl.level = saved[1]
    jl.propagate = saved[2]


def test_emits_severity_and_body(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").warning("hello %s", "world")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    lr = logs[0].log_record
    assert lr.severity_text == "WARN"
    assert lr.severity_number == SeverityNumber.WARN
    assert lr.body == "hello world"


def test_trace_correlation(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("outside")  # no active span
    tp = TracerProvider()
    tracer = tp.get_tracer("t")
    with tracer.start_as_current_span("s") as span:
        sc = span.get_span_context()
        logging.getLogger("jiuwenclaw").info("inside")
    logs = exporter.get_finished_logs()
    assert len(logs) == 2
    assert not logs[0].log_record.trace_id  # outside span -> 0/invalid
    assert logs[1].log_record.trace_id == sc.trace_id  # inside span -> correlated


def test_extra_attributes(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("msg", extra={"user_visible": "progress", "host": "h1"})
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.attributes["user_visible"] == "progress"
    assert lr.attributes["host"] == "h1"
    assert lr.attributes["log.logger"] == "jiuwenclaw"
    assert lr.attributes["code.function"] == "test_extra_attributes"


def test_event_name(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(lp_logger, level="INFO"))
    logging.getLogger("jiuwenclaw").info("msg", extra={"event_name": "msg.received"})
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.attributes["event.name"] == "msg.received"
    assert lr.event_name == "msg.received"  # native field for Phoenix


def test_excluded_logger(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    h = OTelLogHandler(lp_logger, level="INFO", excluded_loggers=("jiuwenclaw.interface.resp",))
    clean_jiuwenclaw_logger.addHandler(h)
    logging.getLogger("jiuwenclaw.interface.resp").info("resp line")  # excluded
    logging.getLogger("jiuwenclaw").info("kept")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    assert logs[0].log_record.attributes["log.logger"] == "jiuwenclaw"


def test_emit_never_raises(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    class _Broken:
        def emit(self, *a, **k):
            raise RuntimeError("boom")
    clean_jiuwenclaw_logger.addHandler(OTelLogHandler(_Broken(), level="INFO"))
    logging.getLogger("jiuwenclaw").info("ok")  # must not raise
    assert exporter.get_finished_logs() == ()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_logs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.logs'`

- [ ] **Step 3: 实现** — 创建 `src/jiuwenswarm_instrumentor/instrumentors/logs.py`:

```python
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
    10: SeverityNumber.DEBUG,
    20: SeverityNumber.INFO,
    30: SeverityNumber.WARN,
    40: SeverityNumber.ERROR,
    50: SeverityNumber.FATAL,
}
_SEVERITY_TEXT = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "FATAL"}

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
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_logs.py -v`
Expected: PASS(6 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/logs.py tests/instrumentors/test_logs.py
git commit -m "feat: OTelLogHandler bridges stdlib logging -> OTel logs"
```

---

### Task 4: instrument_logs — 挂载 / filter 复用 / setup_logger patch / 幂等

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/logs.py`
- Test: `tests/instrumentors/test_logs.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/instrumentors/test_logs.py` 顶部 import 行改为:

```python
import logging
import sys
import types

import pytest
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter, SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider

from jiuwenswarm_instrumentor.instrumentors.logs import OTelLogHandler, instrument_logs
```

在文件末尾追加 4 个用例:

```python
def test_filter_piggyback(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    class RedactFilter(logging.Filter):
        def filter(self, record):
            record.msg = record.msg.replace("secret", "***")
            return True
    pre = logging.StreamHandler()
    pre.addFilter(RedactFilter())
    clean_jiuwenclaw_logger.addHandler(pre)
    instrument_logs(otel_logger=lp_logger, level="INFO")
    logging.getLogger("jiuwenclaw").info("hello secret world")
    lr = exporter.get_finished_logs()[0].log_record
    assert lr.body == "hello *** world"
    ours = [h for h in clean_jiuwenclaw_logger.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1
    assert any(isinstance(f, RedactFilter) for f in ours[0].filters)


def test_no_filters_warning_fallback(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    instrument_logs(otel_logger=lp_logger, level="INFO")  # no pre-existing filters
    logging.getLogger("jiuwenclaw").info("dropped")
    logging.getLogger("jiuwenclaw").warning("kept")
    logs = exporter.get_finished_logs()
    assert len(logs) == 1
    assert logs[0].log_record.severity_text == "WARN"


def test_setup_logger_reattach(otel_logger, clean_jiuwenclaw_logger, monkeypatch):
    lp_logger, exporter = otel_logger
    jl = logging.getLogger("jiuwenclaw")

    def fake_setup_logger():
        jl.handlers = []  # simulates jiuwenclaw clearing handlers

    fake_mod = types.ModuleType("jiuwenclaw.utils")
    fake_mod.setup_logger = fake_setup_logger
    pkg = types.ModuleType("jiuwenclaw")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "jiuwenclaw", pkg)
    monkeypatch.setitem(sys.modules, "jiuwenclaw.utils", fake_mod)

    instrument_logs(otel_logger=lp_logger, level="INFO")
    assert any(getattr(h, "_jiuwenswarm_otel", False) for h in jl.handlers)
    fake_mod.setup_logger()  # wrapped: clears then re-attaches
    assert any(getattr(h, "_jiuwenswarm_otel", False) for h in jl.handlers)


def test_idempotent(otel_logger, clean_jiuwenclaw_logger):
    lp_logger, exporter = otel_logger
    instrument_logs(otel_logger=lp_logger, level="INFO")
    instrument_logs(otel_logger=lp_logger, level="INFO")
    ours = [h for h in clean_jiuwenclaw_logger.handlers if getattr(h, "_jiuwenswarm_otel", False)]
    assert len(ours) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_logs.py::test_filter_piggyback -v`
Expected: FAIL — `ImportError: cannot import name 'instrument_logs'`

- [ ] **Step 3: 实现** — 在 `logs.py` 末尾追加:

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_logs.py -v`
Expected: PASS(全部 10 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/logs.py tests/instrumentors/test_logs.py
git commit -m "feat: instrument_logs attaches handler, piggybacks filters, re-attaches after setup_logger"
```

---

### Task 5: 接入 apply_instrumentors + activate 日志

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/__init__.py`
- Modify: `src/jiuwenswarm_instrumentor/activate.py`
- Test: `tests/instrumentors/test_apply.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/instrumentors/test_apply.py` 末尾追加:

```python
def test_apply_instrumentors_calls_logs_when_configured(monkeypatch):
    called = []
    monkeypatch.setattr(
        "jiuwenswarm_instrumentor.instrumentors.logs.instrument_logs",
        lambda *a, **k: called.append(True),
    )
    for name in ("llm", "tool", "agent", "session"):
        monkeypatch.setattr(
            f"jiuwenswarm_instrumentor.instrumentors.{name}.instrument_{name}",
            lambda *a, _n=name, **k: None,
        )
    from jiuwenswarm_instrumentor.config import InstrumentorConfig
    from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors
    cfg = InstrumentorConfig(enabled=True, logs_exporter="otlp")
    apply_instrumentors(tracer=object(), meter=Mock(), cfg=cfg)
    assert called == [True]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_apply.py::test_apply_instrumentors_calls_logs_when_configured -v`
Expected: FAIL — `apply_instrumentors` 未调用 `logs.instrument_logs`(`called == []`)或 `AttributeError: module has no attribute logs`

- [ ] **Step 3: 实现** — 在 `instrumentors/__init__.py` 的 import 行改为:

```python
from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session, logs
```

在 `apply_instrumentors` 的 `for label, fn in (...)` 循环**之后**追加条件 logs 步(不动原循环):

```python
    if getattr(cfg, "logs_exporter", "none") != "none":
        try:
            logs.instrument_logs(
                level=getattr(cfg, "log_level", "INFO"),
                excluded_loggers=getattr(cfg, "log_excluded_loggers", ()),
                message_max_length=getattr(cfg, "log_message_max_length", 8192),
            )
            logger.info("[instrumentor] applied logs")
        except Exception:
            logger.exception("[instrumentor] failed to apply logs — skipping")
```

在 `activate.py` 的 `activate()` 启动日志行改为:

```python
        logger.info("[instrumentor] active: traces=%s metrics=%s logs=%s endpoint=%s",
                    cfg.traces_exporter, cfg.metrics_exporter, cfg.logs_exporter, cfg.traces_endpoint)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_apply.py -v`
Expected: PASS(全部,含新 1 例;原 `test_apply_instrumentors_invokes_each` 用 cfg=None 仍不触发 logs,保持通过)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/__init__.py src/jiuwenswarm_instrumentor/activate.py tests/instrumentors/test_apply.py
git commit -m "feat: wire logs instrumentor into apply_instrumentors + activate log line"
```

---

### Task 6: 启动指南 + 全量回归

**Files:**
- Modify: `docs/guides/start-jiuwenswarm-with-instrumentor.md`

- [ ] **Step 1: 更新启动指南** — 在 `docs/guides/start-jiuwenswarm-with-instrumentor.md` 的 §2.1 环境变量块里,`OTEL_LOG_MESSAGES=true` 行之后加:

```bash
export OTEL_LOGS_EXPORTER=otlp              # 采集 jiuwenclaw stdlib 日志
export OTEL_LOGS_LEVEL=INFO                 # 采集级别 (DEBUG 会爆量)
# 可选: export OTEL_LOGS_EXCLUDED_LOGGERS=jiuwenclaw.interface.resp
```

在 §3 验证的 "应看到的 span" 表之后加一节:

```markdown
### logs(新)

labubu UI 左侧 **Logs** 页(`/logs`):可见 jiuwenclaw 日志,按 severity / event_name / trace_id 过滤,body 全文搜索。
打开任一 trace,其详情下显示该请求执行期间的关联日志(labubu `GET /api/v1/logs/:traceId`)。
agent/LLM/tool 执行期间的日志带 trace_id(挂在 trace 下);网关路由前等日志无 trace_id,作为独立日志入库。
```

在 §4 常见坑追加一条:

```markdown
- **labubu Logs 页没数据**:确认 `OTEL_LOGS_EXPORTER=otlp`(默认 `none` 不采);确认 labubu `POST /v1/logs` 可达(`curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:4318/v1/logs` 应 200)。`OTEL_LOGS_LEVEL=DEBUG` 会爆量,默认 INFO。
- **日志里没 prompt 等敏感字段被脱敏**:正常 —— instrumentor 复用了 jiuwenclaw 自有的 `SensitiveDataFilter`(从 `jiuwenclaw` logger 已有 handler 复制);若 jiuwenclaw 没装 filter,instrumentor 回退到 WARNING-only(不发 INFO)。
```

- [ ] **Step 2: 跑全量测试**

Run: `py -3.13 -m pytest`
Expected: PASS(全部既有 + 新增 logs/config/provider/apply 用例)

- [ ] **Step 3: console 冒烟(可选,验证端到端)**

Run:
```bash
OTEL_ENABLED=true OTEL_LOGS_EXPORTER=console OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none \
py -3.13 -c "
import logging
from jiuwenswarm_instrumentor import setup
setup()
logging.getLogger('jiuwenclaw').info('hello from smoke')
logging.getLogger('jiuwenclaw').warning('warn line')
"
```
Expected: 控制台输出 `LogRecord`(JSON),severity/body 正确。注:在 instrumentor 自己的测试环境(jiuwenclaw 未安装 → 无 `SensitiveDataFilter` → WARNING 回退)下,INFO 会被丢弃,只输出 WARN 一条;在真实 jiuwenswarm 环境(jiuwenclaw 已装、有脱敏 filter)下,INFO + WARN 两条都会输出。

- [ ] **Step 4: 提交**

```bash
git add docs/guides/start-jiuwenswarm-with-instrumentor.md
git commit -m "docs: logs env + verification in start guide"
```

---

## Self-Review (已执行)

- **Spec 覆盖**:§1 架构 → Task 1-5;§2 范围(jiuwenclaw only)→ 全程;§3 fail-soft/幂等/trace 关联 → Task 3-4;§4 配置 → Task 1;§5 组件 → Task 2-5;§6 错误处理 → Task 2-4 try/except;§7 测试 10 例 → Task 3-4;§8 验收 → Task 6。无遗漏。
- **Placeholder 扫描**:无 TBD/TODO,每步含完整代码与命令。
- **类型一致**:`OTelLogHandler(otel_logger, *, level, excluded_loggers, message_max_length)` 在 Task 3 定义、Task 4 `instrument_logs` 调用签名一致;`instrument_logs(otel_logger=None, *, level, excluded_loggers, message_max_length)` 在 Task 4 定义、Task 5 `apply_instrumentors` 调用一致;`_jiuwenswarm_otel` 标记、`_jiuwenswarm_wrapped` 标记前后一致。
- **API 校准**:trace 关联用 `context=get_current()`(非 deprecated 的 `trace_id=`);`event.name` 设为 attribute(满足 labubu v0.20.0 proto);severity 用 public `SeverityNumber`;导出器路径 `_log_exporter`、endpoint `/v1/logs`。
