# jiuwenswarm-instrumentor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, self-contained OpenTelemetry auto-instrumentation Python package that monkey-patches jiuwenclaw / openjiuwen core runtime APIs to emit GenAI **traces + metrics** via OTLP — with **zero dependency** on the soon-to-be-deleted `jiuwenclaw/telemetry/` module or any of its extension points.

**Architecture:** In-process auto-instrumentation (no jiuwenclaw source edits). At activation time we wrap four core surfaces directly: `OpenAIModelClient.invoke/.stream` (LLM), `ReActAgent.invoke` (agent), `AbilityManager.execute_single` (tool), `JiuWenClaw.create_instance/.cleanup` (session). Wrappers feed a self-contained OTel SDK (`TracerProvider` + `MeterProvider` + OTLP gRPC/HTTP exporters) driven by standard `OTEL_*` env vars. Activation is via a CLI wrapper (`jiuwen-instrument <cmd>`) or explicit `setup()`, applied **before** jiuwenclaw constructs agents (required by openjiuwen's agent metaclass). All patching is fail-soft.

**Tech Stack:** Python 3.11–3.13, `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-{grpc,http}`, `pytest`+`pytest-asyncio`. Instrumented targets: `openjiuwen==0.1.10` (the enterprise_dev-pinned version installed in `D:/code/opensource/gitcode/jiuwenswarm/.venv_enterprise_dev`), `jiuwenclaw` (enterprise_dev branch). Optional: `tiktoken` for token estimation.

**Spec:** `docs/superpowers/specs/2026-06-24-jiuwenswarm-instrumentor-design.md`

---

## File Structure

```
pyproject.toml                      # packaging, deps, entry point `jiuwen-instrument`
README.md                           # usage (already exists)
src/jiuwenswarm_instrumentor/
  __init__.py                       # public API: setup()
  _version.py                        # __version__
  config.py                          # InstrumentorConfig, load_config() — OTEL_* env driven
  attributes.py                      # gen_ai.* + jiuwenclaw.* constant strings
  metrics.py                         # Metrics class: instruments + record_* helpers
  context.py                         # request/session ContextVar + identity attrs
  provider.py                        # init_providers(cfg): TracerProvider + MeterProvider + OTLP/console
  wrap.py                             # patch_method(cls, name, factory) — fail-soft monkey-patch
  activate.py                         # activate(): config -> providers -> apply_instrumentors
  instrumentors/
    __init__.py                       # apply_instrumentors(tracer, meter, cfg)
    llm.py                            # instrument_llm(): wrap OpenAIModelClient.invoke/.stream
    tool.py                           # instrument_tool(): wrap AbilityManager.execute_single
    agent.py                          # instrument_agent(): wrap ReActAgent.invoke
    session.py                        # instrument_session(): wrap JiuWenClaw.create_instance/.cleanup
tests/
  conftest.py                        # collecting span exporter, test tracer/meter, reset globals
  test_config.py
  test_wrap.py
  test_attributes.py
  test_metrics.py
  test_context.py
  test_provider.py
  instrumentors/
    test_llm.py
    test_tool.py
    test_agent.py
    test_session.py
    test_apply.py
```

Each module has one responsibility and is independently testable. Instrumentors receive `tracer`/`meter` (dependency injection) so they can be unit-tested with fakes instead of real openjiuwen.

**Pinned targets (openjiuwen 0.1.10 / jiuwenclaw enterprise_dev):**

| Surface | Class.method | File:line |
|---|---|---|
| LLM (non-stream) | `OpenAIModelClient.invoke` | `openjiuwen/core/foundation/llm/model_clients/openai_model_client.py:144` |
| LLM (stream) | `OpenAIModelClient.stream` | `…/openai_model_client.py:283` |
| Tool | `AbilityManager.execute_single` | `openjiuwen/core/single_agent/ability_manager.py:635` |
| Agent | `ReActAgent.invoke` | `openjiuwen/core/single_agent/agents/react_agent.py:1506` |
| Session start | `JiuWenClaw.create_instance` | `jiuwenclaw/agentserver/interface.py:412` |
| Session end | `JiuWenClaw.cleanup` | `jiuwenclaw/agentserver/interface.py:1728` |

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/jiuwenswarm_instrumentor/__init__.py`
- Create: `src/jiuwenswarm_instrumentor/_version.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "jiuwenswarm-instrumentor"
version = "0.1.0"
description = "Standalone OpenTelemetry auto-instrumentation for jiuwenclaw / openjiuwen"
requires-python = ">=3.11,<3.14"
dependencies = [
    "opentelemetry-api>=1.25.0",
    "opentelemetry-sdk>=1.25.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.25.0",
    "opentelemetry-exporter-otlp-proto-http>=1.25.0",
]

[project.optional-dependencies]
estimate = ["tiktoken>=0.7.0"]
test = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
jiuwen-instrument = "jiuwenswarm_instrumentor.activate:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `_version.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Write `__init__.py`**

```python
from jiuwenswarm_instrumentor._version import __version__

__all__ = ["__version__"]
```

- [ ] **Step 4: Write `tests/conftest.py` (collecting span exporter — dependency-free)**

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry import trace


class CollectingSpanExporter(SpanExporter):
    """Collects spans in a list for test assertions."""
    def __init__(self):
        self.spans = []
    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass


def reset_tracing(exporter):
    """Install a fresh global TracerProvider that feeds `exporter`. Returns the exporter."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)  # set_tracer_provider raises if already set → fresh process per test
    return exporter
```

> Note: `trace.set_tracer_provider` raises if called twice in one process. Tests that need isolation use subprocesses or the CLI/activate path (Task 12). For unit tests we rely on the FIRST set in the process; `pytest-xdist` (one worker per test) is not required for the minimal cases below because we set the provider once. If flakiness appears, switch affected tests to construct a local `TracerProvider` and pass its tracer via DI (instrumentors accept a `tracer`).

- [ ] **Step 5: Write `tests/test_smoke.py`**

```python
from jiuwenswarm_instrumentor import __version__


def test_version():
    assert __version__ == "0.1.0"
```

- [ ] **Step 6: Install + run**

Run: `cd "D:/code/opensource/github/jiuwenswarm-instrumentor" && pip install -e ".[test]"`
Then: `python -m pytest tests/test_smoke.py -v`
Expected: PASS (1 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: scaffold jiuwenswarm-instrumentor package"
```

---

## Task 2: Config (`config.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
import pytest
from jiuwenswarm_instrumentor.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("OTEL_"):
            monkeypatch.delenv(k, raising=False)


def test_disabled_by_default():
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.traces_exporter == "none"
    assert cfg.protocol == "grpc"


def test_env_overrides():
    os.environ["OTEL_ENABLED"] = "true"
    os.environ["OTEL_TRACES_EXPORTER"] = "otlp"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http"
    os.environ["OTEL_SERVICE_NAME"] = "jiuwenclaw-prod"
    try:
        cfg = load_config()
    finally:
        del os.environ["OTEL_ENABLED"]; del os.environ["OTEL_TRACES_EXPORTER"]
        del os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]; del os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"]
        del os.environ["OTEL_SERVICE_NAME"]
    assert cfg.enabled is True
    assert cfg.traces_exporter == "otlp"
    assert cfg.traces_endpoint == "http://localhost:4317"
    assert cfg.traces_protocol == "http"
    assert cfg.service_name == "jiuwenclaw-prod"
    assert cfg.log_messages is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.config'`

- [ ] **Step 3: Implement `config.py`**

```python
# src/jiuwenswarm_instrumentor/config.py
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentorConfig:
    enabled: bool = False
    traces_exporter: str = "none"        # otlp | console | none
    traces_endpoint: str = "http://localhost:4317"
    traces_protocol: str = "grpc"        # grpc | http
    traces_headers: dict = None
    metrics_exporter: str = "none"
    metrics_endpoint: str = "http://localhost:4317"
    metrics_protocol: str = "grpc"
    metrics_headers: dict = None
    protocol: str = "grpc"
    service_name: str = "jiuwenclaw"
    log_messages: bool = False           # opt-in full prompt/response capture
    message_max_length: int = 4096


def _str(key, default):
    v = os.getenv(key)
    return (v or "").strip() or default


def _bool(key, default):
    v = os.getenv(key)
    return default if v is None else v.strip().lower() in ("true", "1", "yes")


def _lower(key, default):
    v = os.getenv(key)
    return (v or "").strip().lower() or default


def _headers(key):
    raw = os.getenv(key, "")
    out = {}
    for item in raw.split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_config() -> InstrumentorConfig:
    protocol = _lower("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    endpoint = _str("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    return InstrumentorConfig(
        enabled=_bool("OTEL_ENABLED", False),
        traces_exporter=_lower("OTEL_TRACES_EXPORTER", "none"),
        traces_endpoint=_str("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        traces_protocol=_lower("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", protocol),
        traces_headers=_headers("OTEL_EXPORTER_OTLP_TRACES_HEADERS"),
        metrics_exporter=_lower("OTEL_METRICS_EXPORTER", "none"),
        metrics_endpoint=_str("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", endpoint),
        metrics_protocol=_lower("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", protocol),
        metrics_headers=_headers("OTEL_EXPORTER_OTLP_METRICS_HEADERS"),
        protocol=protocol,
        service_name=_str("OTEL_SERVICE_NAME", "jiuwenclaw"),
        log_messages=_bool("OTEL_LOG_MESSAGES", False),
        message_max_length=int(_str("OTEL_MESSAGE_CONTENT_MAX_LENGTH", "4096") or 4096),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/config.py tests/test_config.py
git commit -m "feat: env-driven InstrumentorConfig"
```

---

## Task 3: Attributes (`attributes.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/attributes.py`
- Test: `tests/test_attributes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_attributes.py
from jiuwenswarm_instrumentor import attributes as A


def test_gen_ai_constants():
    assert A.GEN_AI_SYSTEM == "gen_ai.system"
    assert A.GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert A.GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert A.GEN_AI_TOOL_NAME == "gen_ai.tool.name"


def test_jiuwenclaw_constants():
    assert A.JIUWENCLAW_SESSION_ID == "jiuwenclaw.session.id"
    assert A.JIUWENCLAW_CHANNEL_ID == "jiuwenclaw.channel.id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_attributes.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `attributes.py`**

```python
# src/jiuwenswarm_instrumentor/attributes.py
"""OTel GenAI semantic-convention attribute keys (subset used by this package)."""

# GenAI
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_STREAMING = "gen_ai.request.streaming"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS = "gen_ai.usage.reasoning.output_tokens"
GEN_AI_USAGE_ESTIMATED = "gen_ai.usage.estimated"
GEN_AI_TOKEN_TYPE = "gen_ai.token.type"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_ARGUMENTS = "gen_ai.tool.arguments"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_STREAMING_FIRST_TOKEN_MS = "gen_ai.streaming.first_token_ms"

# jiuwenclaw custom dimensions
JIUWENCLAW_SESSION_ID = "jiuwenclaw.session.id"
JIUWENCLAW_CHANNEL_ID = "jiuwenclaw.channel.id"
JIUWENCLAW_REQUEST_ID = "jiuwenclaw.request.id"
JIUWENCLAW_AGENT_NAME = "jiuwenclaw.agent.name"
JIUWENCLAW_DOMAIN_ID = "jiuwenclaw.domain.id"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_attributes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/attributes.py tests/test_attributes.py
git commit -m "feat: gen_ai + jiuwenclaw attribute constants"
```

---

## Task 4: fail-soft wrap helper (`wrap.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/wrap.py`
- Test: `tests/test_wrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wrap.py
import pytest
from jiuwenswarm_instrumentor.wrap import patch_method


class _Fake:
    async def invoke(self, x):
        return x + 1


async def test_patch_wraps_method():
    fake_cls = _Fake

    def factory(original):
        async def wrapped(self, x):
            return await original(self, x) * 10
        return wrapped

    applied = patch_method(fake_cls, "invoke", factory)
    assert applied is True
    assert await fake_cls().invoke(2) == 30  # (2+1)*10


async def test_patch_idempotent():
    fake_cls = _Fake

    def factory(original):
        async def wrapped(self, x):
            return await original(self, x)
        return wrapped

    patch_method(fake_cls, "invoke", factory)
    second = patch_method(fake_cls, "invoke", factory)
    assert second is False  # already wrapped, skipped


def test_patch_missing_method_returns_false():
    class Empty:
        pass
    assert patch_method(Empty, "nope", lambda o: o) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wrap.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `wrap.py`**

```python
# src/jiuwenswarm_instrumentor/wrap.py
from __future__ import annotations
import logging

logger = logging.getLogger("jiuwenswarm_instrumentor")

_WRAPPED_ATTR = "_jiuwenswarm_wrapped"


def patch_method(cls, name, factory):
    """Monkey-patch cls.<name> with factory(original).

    factory(original_callable) -> replacement_callable. Sets the replacement on the class.
    Idempotent and fail-soft: returns True if applied, False if skipped (missing attr or
    already wrapped). Never raises into the host application.
    """
    original = getattr(cls, name, None)
    if original is None:
        logger.warning("[instrumentor] %s.%s not found — skipping", _qualname(cls), name)
        return False
    if getattr(original, _WRAPPED_ATTR, False):
        return False
    try:
        wrapper = factory(original)
    except Exception:
        logger.exception("[instrumentor] failed to build wrapper for %s.%s — skipping", _qualname(cls), name)
        return False
    setattr(wrapper, _WRAPPED_ATTR, True)
    setattr(wrapper, "__wrapped__", original)
    setattr(cls, name, wrapper)
    return True


def _qualname(cls):
    return f"{getattr(cls, '__module__', '?')}.{getattr(cls, '__qualname__', repr(cls))}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wrap.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/wrap.py tests/test_wrap.py
git commit -m "feat: fail-soft patch_method helper"
```

---

## Task 5: Metrics (`metrics.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
from unittest.mock import Mock
from jiuwenswarm_instrumentor.metrics import Metrics


def test_record_token_usage_calls_counter():
    meter = Mock()
    m = Metrics(meter)
    m.record_token_usage(input_tokens=10, output_tokens=5, attrs={"gen_ai.request.model": "gpt-x"})
    counter = meter.create_counter.return_value
    counter.add.assert_any_call(10, {"gen_ai.request.model": "gpt-x", "gen_ai.token.type": "input"})
    counter.add.assert_any_call(5, {"gen_ai.request.model": "gpt-x", "gen_ai.token.type": "output"})


def test_record_llm_duration_calls_histogram():
    meter = Mock()
    m = Metrics(meter)
    m.record_llm_duration(1.5, {"gen_ai.system": "openai"})
    meter.create_histogram.return_value.record.assert_called_once_with(1.5, {"gen_ai.system": "openai"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `metrics.py`**

```python
# src/jiuwenswarm_instrumentor/metrics.py
from __future__ import annotations


class Metrics:
    """Owns OTel metric instruments + recording helpers."""

    def __init__(self, meter):
        self._token_usage = meter.create_counter(
            "gen_ai.client.token.usage", unit="{token}",
            description="Tokens consumed by GenAI model calls, by type",
        )
        self._llm_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s",
            description="LLM call duration in seconds",
        )
        self._tool_duration = meter.create_histogram(
            "gen_ai.tool.duration", unit="s",
            description="Tool execution duration in seconds",
        )
        self._tool_calls = meter.create_counter(
            "gen_ai.tool.count", unit="1",
            description="Number of tool executions",
        )
        self._agent_duration = meter.create_histogram(
            "gen_ai.agent.duration", unit="s",
            description="Agent invocation duration in seconds",
        )

    def record_token_usage(self, input_tokens, output_tokens, attrs):
        base = dict(attrs)
        self._token_usage.add(int(input_tokens or 0), {**base, "gen_ai.token.type": "input"})
        self._token_usage.add(int(output_tokens or 0), {**base, "gen_ai.token.type": "output"})

    def record_llm_duration(self, seconds, attrs):
        self._llm_duration.record(seconds, attrs)

    def record_tool(self, seconds, attrs):
        self._tool_calls.add(1, attrs)
        self._tool_duration.record(seconds, attrs)

    def record_agent_duration(self, seconds, attrs):
        self._agent_duration.record(seconds, attrs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/metrics.py tests/test_metrics.py
git commit -m "feat: Metrics instruments + helpers"
```

---

## Task 6: Context propagation (`context.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs


def test_context_roundtrip():
    token = set_request_context(session_id="s1", channel_id="c1", request_id="r1")
    try:
        attrs = current_request_attrs()
        assert attrs["jiuwenclaw.session.id"] == "s1"
        assert attrs["jiuwenclaw.channel.id"] == "c1"
        assert attrs["jiuwenclaw.request.id"] == "r1"
    finally:
        token.reset()


def test_context_empty():
    assert current_request_attrs() == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_context.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `context.py`**

```python
# src/jiuwenswarm_instrumentor/context.py
from __future__ import annotations
from contextvars import ContextVar

from jiuwenswarm_instrumentor import attributes as A

_request_context: ContextVar[dict | None] = ContextVar("jiuwenswarm_request_context", default=None)


def set_request_context(*, session_id=None, channel_id=None, request_id=None, agent_name=None):
    current = dict(_request_context.get() or {})
    if session_id is not None:
        current[A.JIUWENCLAW_SESSION_ID] = session_id
    if channel_id is not None:
        current[A.JIUWENCLAW_CHANNEL_ID] = channel_id
    if request_id is not None:
        current[A.JIUWENCLAW_REQUEST_ID] = request_id
    if agent_name is not None:
        current[A.JIUWENCLAW_AGENT_NAME] = agent_name
    return _request_context.set(current)


def current_request_attrs() -> dict:
    return dict(_request_context.get() or {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_context.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/context.py tests/test_context.py
git commit -m "feat: request/session context propagation"
```

---

## Task 7: Provider (`provider.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/provider.py`
- Test: `tests/test_provider.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provider.py
from unittest.mock import patch
from jiuwenswarm_instrumentor.config import InstrumentorConfig
from jiuwenswarm_instrumentor import provider


def test_disabled_returns_none():
    cfg = InstrumentorConfig(enabled=False)
    assert provider.init_providers(cfg) is None


def test_otlp_http_builds_providers(monkeypatch):
    monkeypatch.setattr(provider.trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(provider.metrics, "set_meter_provider", lambda p: None)
    cfg = InstrumentorConfig(
        enabled=True, traces_exporter="otlp", traces_protocol="http",
        traces_endpoint="http://localhost:4318", service_name="jc",
    )
    tp, mp = provider.init_providers(cfg)
    assert tp is not None and mp is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_provider.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `provider.py`**

```python
# src/jiuwenswarm_instrumentor/provider.py
from __future__ import annotations
import logging

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter

from jiuwenswarm_instrumentor.config import InstrumentorConfig

logger = logging.getLogger("jiuwenswarm_instrumentor")


def init_providers(cfg: InstrumentorConfig):
    """Build + install TracerProvider & MeterProvider. Returns (tracer_provider, meter_provider)
    or None if disabled. Fail-soft: logs and returns None on error."""
    if not cfg.enabled:
        return None
    try:
        resource = Resource.create({SERVICE_NAME: cfg.service_name, "telemetry.sdk.language": "python"})
        tp = TracerProvider(resource=resource)
        mp = MeterProvider(resource=resource, metric_readers=_metric_readers(cfg))
        _attach_traces(tp, cfg)
        try:
            trace.set_tracer_provider(tp)
        except Exception:
            pass  # already set in-process; ignore
        try:
            metrics.set_meter_provider(mp)
        except Exception:
            pass
        return tp, mp
    except Exception:
        logger.exception("[instrumentor] provider init failed — telemetry disabled")
        return None


def _attach_traces(tp, cfg):
    if cfg.traces_exporter == "otlp":
        tp.add_span_processor(BatchSpanProcessor(_otlp_span_exporter(cfg)))
    elif cfg.traces_exporter == "console":
        tp.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))


def _metric_readers(cfg):
    readers = []
    if cfg.metrics_exporter == "otlp":
        readers.append(PeriodicExportingMetricReader(_otlp_metric_exporter(cfg), export_interval_millis=30000))
    elif cfg.metrics_exporter == "console":
        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=3000))
    return readers


def _otlp_span_exporter(cfg):
    if cfg.traces_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=f"{cfg.traces_endpoint}/v1/traces", headers=cfg.traces_headers)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    return OTLPSpanExporter(endpoint=cfg.traces_endpoint, headers=cfg.traces_headers)


def _otlp_metric_exporter(cfg):
    if cfg.metrics_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=f"{cfg.metrics_endpoint}/v1/metrics", headers=cfg.metrics_headers)
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    return OTLPMetricExporter(endpoint=cfg.metrics_endpoint, headers=cfg.metrics_headers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_provider.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/provider.py tests/test_provider.py
git commit -m "feat: self-contained OTel providers + OTLP exporters"
```

---

## Task 8: LLM instrumentor (`instrumentors/llm.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/__init__.py` (empty placeholder for now)
- Create: `src/jiuwenswarm_instrumentor/instrumentors/llm.py`
- Test: `tests/instrumentors/__init__.py` (empty)
- Test: `tests/instrumentors/test_llm.py`

- [ ] **Step 1: Write the failing test with a fake model client**

```python
# tests/instrumentors/test_llm.py
import types
from unittest.mock import Mock
import pytest

from jiuwenswarm_instrumentor.instrumentors.llm import instrument_llm
from jiuwenswarm_instrumentor.metrics import Metrics
from jiuwenswarm_instrumentor import context


class _Usage:
    def __init__(self, i, o, t, c=0):
        self.input_tokens = i; self.output_tokens = o; self.total_tokens = t; self.cache_tokens = c


class _Assistant:
    def __init__(self, content="hi", usage=None, finish_reason="stop"):
        self.content = content
        self.usage_metadata = usage
        self.finish_reason = finish_reason
        self.tool_calls = None
        self.reasoning_content = None


def _make_fake_client_cls():
    class _ModelConfig:
        model_name = "gpt-x"; temperature = 0.7; top_p = None
    class _ClientConfig:
        client_provider = "OpenAI"

    class FakeModelClient:
        model_config = _ModelConfig()
        model_client_config = _ClientConfig()

        async def invoke(self, messages, *, tools=None, temperature=None, top_p=None,
                         model=None, max_tokens=None, stop=None, output_parser=None, timeout=None, **kw):
            return _Assistant(usage=_Usage(12, 8, 20))

        async def stream(self, messages, **kw):
            yield _Assistant(content="he", usage=None, finish_reason="null")
            yield _Assistant(content="llo", usage=_Usage(5, 3, 8), finish_reason="stop")
    return FakeModelClient


async def test_invoke_creates_genai_span(exporter):
    from opentelemetry import trace
    from jiuwenswarm_instrumentor.conftest import reset_tracing  # noqa
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)

    client = Fake()
    ctx_token = context.set_request_context(session_id="s1", channel_id="c1")
    try:
        await client.invoke([{"role": "user", "content": "hi"}])
    finally:
        ctx_token.reset()

    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "gen_ai.chat"
    assert span.attributes["gen_ai.request.model"] == "gpt-x"
    assert span.attributes["gen_ai.usage.input_tokens"] == 12
    assert span.attributes["gen_ai.usage.output_tokens"] == 8
    assert span.attributes["jiuwenclaw.session.id"] == "s1"
```

> Add `exporter` fixture to `tests/conftest.py` (replace/extend the file):

```python
# tests/conftest.py  (append)
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


@pytest.fixture
def exporter():
    from tests.conftest import CollectingSpanExporter  # defined in Task 1
    exp = CollectingSpanExporter()
    # set the global provider exactly once per process; subsequent tests reuse it
    try:
        trace.set_tracer_provider(TracerProvider())
    except Exception:
        pass
    tp = trace.get_tracer_provider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    return exp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/instrumentors/test_llm.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `instrumentors/llm.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/llm.py
from __future__ import annotations
import time

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/instrumentors/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors tests/instrumentors tests/conftest.py
git commit -m "feat: LLM instrumentor (OpenAIModelClient.invoke/.stream)"
```

---

## Task 9: Tool instrumentor (`instrumentors/tool.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/tool.py`
- Test: `tests/instrumentors/test_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/instrumentors/test_tool.py
from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.tool import instrument_tool
from jiuwenswarm_instrumentor.metrics import Metrics


class _ToolCall:
    name = "search"; id = "tc1"; arguments = '{"q": "x"}'


class _ToolMsg:
    def __init__(self):
        self.content = "ok"; self.tool_call_id = "tc1"; self.metadata = {}


class _Ctx:
    pass


def _fake_ability_cls():
    class FakeAbility:
        async def execute_single(self, parent_ctx, tool_call, session, tag=None):
            return ("result", _ToolMsg(), _Ctx())
    return FakeAbility


async def test_tool_span(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_ability_cls()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    res = await Fake().execute_single(_Ctx(), _ToolCall(), session=None)
    assert res[1].content == "ok"
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "gen_ai.tool"
    assert span.attributes["gen_ai.tool.name"] == "search"
    assert span.attributes["gen_ai.tool.call.id"] == "tc1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/instrumentors/test_tool.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `instrumentors/tool.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/tool.py
from __future__ import annotations
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def instrument_tool(tracer, metrics, *, ability_cls=None):
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
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.tool", kind=SpanKind.CLIENT, attributes=attrs) as span:
                try:
                    result = await original(self, parent_ctx, tool_call, session, tag=tag)
                    tool_msg = result[1] if isinstance(result, tuple) and len(result) >= 2 else None
                    if tool_msg is not None and getattr(tool_msg, "metadata", None):
                        if tool_msg.metadata.get("is_error"):
                            span.set_status(StatusCode.ERROR)
                    span.set_status(StatusCode.OK)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/instrumentors/test_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/tool.py tests/instrumentors/test_tool.py
git commit -m "feat: tool instrumentor (AbilityManager.execute_single)"
```

---

## Task 10: Agent instrumentor (`instrumentors/agent.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/agent.py`
- Test: `tests/instrumentors/test_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/instrumentors/test_agent.py
from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.agent import instrument_agent
from jiuwenswarm_instrumentor.metrics import Metrics


class _Card:
    id = "agent-1"; name = "JiuwenAgent"


class _Session:
    def get_session_id(self):
        return "sess-9"


def _fake_agent_cls():
    class FakeAgent:
        card = _Card()
        async def invoke(self, inputs, session=None, **kw):
            return {"output": "done", "result_type": "answer"}
    return FakeAgent


async def test_agent_span_and_context(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_agent_cls()
    instrument_agent(tracer, metrics, agent_cls=Fake)
    out = await Fake().invoke({"query": "hi"}, session=_Session())
    assert out["output"] == "done"
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "jiuwenclaw.agent.invoke"
    assert span.attributes["gen_ai.agent.name"] == "JiuwenAgent"
    assert span.attributes["jiuwenclaw.session.id"] == "sess-9"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/instrumentors/test_agent.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `instrumentors/agent.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/agent.py
from __future__ import annotations
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def _session_id(session):
    try:
        return session.get_session_id() if session is not None else None
    except Exception:
        return None


def instrument_agent(tracer, metrics, *, agent_cls=None):
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
            try:
                with tracer.start_as_current_span("jiuwenclaw.agent.invoke", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                    try:
                        result = await original(self, inputs, session, **kwargs)
                        rt = result.get("result_type") if isinstance(result, dict) else None
                        if rt == "error":
                            span.set_status(StatusCode.ERROR)
                        else:
                            span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(StatusCode.ERROR, str(exc)[:256])
                        span.record_exception(exc)
                        raise
            finally:
                metrics.record_agent_duration(time.monotonic() - start,
                                               {A.GEN_AI_AGENT_NAME: agent_name})
                ctx_token.reset()
        return traced

    patch_method(agent_cls, "invoke", factory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/instrumentors/test_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/agent.py tests/instrumentors/test_agent.py
git commit -m "feat: agent instrumentor (ReActAgent.invoke)"
```

---

## Task 11: Session instrumentor (`instrumentors/session.py`)

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/session.py`
- Test: `tests/instrumentors/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/instrumentors/test_session.py
from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.session import instrument_session
from jiuwenswarm_instrumentor.metrics import Metrics


def _fake_jiuwenclaw_cls():
    class FakeJW:
        async def create_instance(self, config=None, *, mode="agent", session_id=None):
            self._session_id = session_id
        async def cleanup(self):
            pass
    return FakeJW


async def test_session_spans(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_jiuwenclaw_cls()
    instrument_session(tracer, metrics, jiuwenclaw_cls=Fake)
    inst = Fake()
    await inst.create_instance(session_id="sess-7")
    await inst.cleanup()
    names = [s.name for s in exporter.spans]
    assert "jiuwenclaw.session.create" in names
    assert "jiuwenclaw.session.end" in names
    create_span = next(s for s in exporter.spans if s.name == "jiuwenclaw.session.create")
    assert create_span.attributes["jiuwenclaw.session.id"] == "sess-7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/instrumentors/test_session.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement `instrumentors/session.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/session.py
from __future__ import annotations

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import set_request_context, current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def instrument_session(tracer, metrics, *, jiuwenclaw_cls=None):
    """Wrap JiuWenClaw.create_instance + .cleanup (jiuwenclaw enterprise_dev, interface.py)."""
    if jiuwenclaw_cls is None:
        from jiuwenclaw.agentserver.interface import JiuWenClaw
        jiuwenclaw_cls = JiuWenClaw

    def create_factory(original):
        async def traced(self, config=None, *, mode="agent", session_id=None, **kw):
            ctx_token = set_request_context(session_id=session_id)
            attrs = {A.JIUWENCLAW_SESSION_ID: session_id or "", "jiuwenclaw.session.mode": mode}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.create", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, config, mode=mode, session_id=session_id, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    ctx_token.reset()
        return traced

    def cleanup_factory(original):
        async def traced(self, *args, **kw):
            sid = getattr(self, "_session_id", None)
            attrs = {A.JIUWENCLAW_SESSION_ID: sid or ""}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.end", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, *args, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
        return traced

    patch_method(jiuwenclaw_cls, "create_instance", create_factory)
    patch_method(jiuwenclaw_cls, "cleanup", cleanup_factory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/instrumentors/test_session.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/session.py tests/instrumentors/test_session.py
git commit -m "feat: session instrumentor (JiuWenClaw lifecycle)"
```

---

## Task 12: Activation + `apply_instrumentors` + CLI (`activate.py`, `instrumentors/__init__.py`, `__init__.py`)

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/__init__.py`
- Create: `src/jiuwenswarm_instrumentor/activate.py`
- Modify: `src/jiuwenswarm_instrumentor/__init__.py`
- Test: `tests/instrumentors/test_apply.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/instrumentors/test_apply.py
import importlib
from unittest.mock import patch
from jiuwenswarm_instrumentor import activate


def test_activate_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    # Should return False and not import openjiuwen/jiuwenclaw
    with patch("jiuwenswarm_instrumentor.activate.init_providers", return_value=None) as ip:
        result = activate.activate()
    assert result is False
    ip.assert_not_called()


def test_apply_instrumentors_invokes_each(monkeypatch):
    calls = []
    for name in ("llm", "tool", "agent", "session"):
        monkeypatch.setattr(
            f"jiuwenswarm_instrumentor.instrumentors.{name}.instrument_{name}",
            lambda *a, **k: calls.append(name),
        )
    from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors
    apply_instrumentors(tracer=object(), meter=object(), cfg=None)
    assert set(calls) == {"llm", "tool", "agent", "session"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/instrumentors/test_apply.py -v`
Expected: FAIL (apply_instrumentors / activate missing)

- [ ] **Step 3: Implement `instrumentors/__init__.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/__init__.py
from __future__ import annotations
import logging

from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session
from jiuwenswarm_instrumentor.metrics import Metrics

logger = logging.getLogger("jiuwenswarm_instrumentor")


def apply_instrumentors(tracer, meter, cfg):
    """Apply all instrumentors. Fail-soft per instrumentor."""
    metrics = Metrics(meter)
    log_messages = getattr(cfg, "log_messages", False)
    for label, fn in (
        ("llm", lambda: llm.instrument_llm(tracer, metrics, log_messages=log_messages)),
        ("tool", lambda: tool.instrument_tool(tracer, metrics)),
        ("agent", lambda: agent.instrument_agent(tracer, metrics)),
        ("session", lambda: session.instrument_session(tracer, metrics)),
    ):
        try:
            fn()
            logger.info("[instrumentor] applied %s", label)
        except Exception:
            logger.exception("[instrumentor] failed to apply %s — skipping", label)
```

- [ ] **Step 4: Implement `activate.py`**

```python
# src/jiuwenswarm_instrumentor/activate.py
from __future__ import annotations
import logging
import runpy
import sys

from opentelemetry import trace, metrics

from jiuwenswarm_instrumentor.config import load_config
from jiuwenswarm_instrumentor.provider import init_providers
from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors

logger = logging.getLogger("jiuwenswarm_instrumentor")
_APPLIED = False


def activate() -> bool:
    """Read config, install providers, apply instrumentors. Idempotent + fail-soft.
    Returns True if instrumentation is active, False if disabled."""
    global _APPLIED
    if _APPLIED:
        return True
    cfg = load_config()
    if not cfg.enabled:
        logger.info("[instrumentor] OTEL_ENABLED not set — instrumentation disabled")
        return False
    try:
        init_providers(cfg)
        apply_instrumentors(trace.get_tracer("jiuwenswarm_instrumentor"),
                            metrics.get_meter("jiuwenswarm_instrumentor"), cfg)
        _APPLIED = True
        logger.info("[instrumentor] active: traces=%s metrics=%s endpoint=%s",
                    cfg.traces_exporter, cfg.metrics_exporter, cfg.traces_endpoint)
        return True
    except Exception:
        logger.exception("[instrumentor] activation failed — running without instrumentation")
        return False


def main():
    """CLI entry point: `jiuwen-instrument <module> [args...]`.

    Activates instrumentation, then runs the target module as __main__. Must run
    BEFORE jiuwenclaw/openjiuwen construct agent instances (openjiuwen agent metaclass
    rebinds invoke at construction time).
    """
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("usage: jiuwen-instrument <module> [args...]", file=sys.stderr)
        sys.exit(2)
    activate()
    runpy.run_module(sys.argv[1], run_name="__main__", alter_sys=True)
```

- [ ] **Step 5: Implement public `setup()` in `__init__.py`**

```python
# src/jiuwenswarm_instrumentor/__init__.py
from jiuwenswarm_instrumentor._version import __version__


def setup():
    """Explicit in-process activation hook. Call once at the earliest startup point."""
    from jiuwenswarm_instrumentor.activate import activate
    return activate()


__all__ = ["__version__", "setup"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/instrumentors/test_apply.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add src/jiuwenswarm_instrumentor tests/instrumentors/test_apply.py
git commit -m "feat: activation, apply_instrumentors, setup() and CLI entry point"
```

---

## Task 13: README usage docs + manual smoke checklist

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append usage to `README.md`**

Append the following sections (keep the existing one-line description at top):

```markdown

## 安装

```bash
cd jiuwenswarm-instrumentor && pip install -e .
```

## 使用（无侵入）

设好环境变量后，用 CLI 包装启动 jiuwenclaw（无需改 jiuwenclaw 源码）：

```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # Phoenix/Langfuse/labubu
export OTEL_SERVICE_NAME=jiuwenclaw
jiuwen-instrument jiuwenclaw-app
```

或在程序入口显式激活（兜底）：

```python
import jiuwenswarm_instrumentor; jiuwenswarm_instrumentor.setup()
```

## 可选项

- `OTEL_LOG_MESSAGES=true` — 记录完整 prompt/response 内容（默认关闭，隐私优先）。
- `OTEL_MESSAGE_CONTENT_MAX_LENGTH` — 单条内容长度上限（默认 4096）。

## 手动冒烟（smoke）

1. 启动后端（如 Phoenix：`python -m phoenix.server serve`）。
2. `OTEL_ENABLED=true OTEL_TRACES_EXPORTER=console jiuwen-instrument jiuwenclaw-app`。
3. 发一条对话，确认控制台 / 后端出现 `gen_ai.chat` / `gen_ai.tool` / `jiuwenclaw.agent.invoke` span。
```

- [ ] **Step 2: Verify the CLI installs**

Run: `jiuwen-instrument --help 2>&1 || python -m jiuwenswarm_instrumentor.activate` (expect a usage/exit or normal run; the entry point is wired by `[project.scripts]`).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: usage + smoke checklist"
```

---

## Self-Review (completed by plan author)

**1. Spec coverage** — every spec section maps to a task:
- Self-contained OTel stack → Task 7; config → Task 2; attributes → Task 3; metrics → Task 5; fail-soft patch → Task 4; context → Task 6; LLM/Tool/Agent/Session instrumentors → Tasks 8–11; activation (CLI + setup) → Task 12; privacy (log_messages default off, length cap) → Tasks 2 & 8; testing → per-task + smoke (Task 13). labubu/Phoenix/Langfuse all OTLP → Task 7 exporter + Task 13 endpoint config. ✓
- Non-goals honored: no logs, no backend, no config.yaml, no officeclaw, no `jiuwenclaw.telemetry.*` import. ✓

**2. Placeholder scan** — no TBD/TODO; every code step contains complete code; all referenced symbols (`InstrumentorConfig`, `Metrics`, `patch_method`, `instrument_*`, `apply_instrumentors`, `activate`, `setup`) are defined in earlier tasks. ✓

**3. Type/name consistency** — `instrument_llm(tracer, metrics, *, log_messages, model_client_cls=...)`, `instrument_tool(tracer, metrics, *, ability_cls=...)`, `instrument_agent(tracer, metrics, *, agent_cls=...)`, `instrument_session(tracer, metrics, *, jiuwenclaw_cls=...)` are consistent across Tasks 8–12 and `apply_instrumentors` (Task 12). `Metrics.record_*` method names match between Task 5 and Tasks 8–10. Attribute constants used in instrumentors all exist in Task 3's `attributes.py`. ✓

**Known limitation (documented, not a placeholder):** the `trace.set_tracer_provider` OTel constraint (set-once-per-process) means tests share a global provider; the `exporter` fixture handles the common case, and instrumentors are written to also accept an injected `tracer` for stricter isolation if needed. Reasoning-token count is unavailable on the parsed `AssistantMessage` (openjiuwen does not surface it) — recorded as a known gap in the spec's risks; not a plan defect.
