# 端到端 trace 上下文传播 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 gateway 与 agentserver 串成一条端到端 trace(W3C traceparent 跨 WS 边界传播),并让两边 jiuwenclaw 日志带同一 trace_id。

**Architecture:** gateway 侧 monkey-patch `MessageHandler.process_message[_stream]`(开 `channel.request` span,全程 current)+ `WebSocketAgentServerClient.send_request[_stream]`(开 `jiuwenclaw.gateway.agent.request` CLIENT span + 注入 traceparent 进 `envelope.channel_context`)。agentserver 侧 monkey-patch `JiuWenClawDeepAdapter.process_message_impl[_stream]`(从 `request.metadata` 提取 traceparent + `context.attach`)→ 现有 `agent.invoke` 的 `start_as_current_span` 自动以 remote parent 为父。零 agent.py 改动;carrier 复用 E2A 通用 `channel_context`/`metadata`,不动 codec。

**Tech Stack:** Python 3.13(`py -3.13`)、opentelemetry-api/sdk(`opentelemetry.propagate.inject/extract` + `opentelemetry.context.attach/detach` + `opentelemetry.trace`)、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-25-trace-context-propagation-design.md`

**关键 API 备注(实测 OTel 1.43.0):**
- `from opentelemetry.propagate import inject, extract` — `inject(carrier)` 把当前 span 的 traceparent 写进 carrier dict(`carrier["traceparent"] = "00-..."`);`extract(carrier)` 返回 `Context`(含 remote SpanContext)。
- `from opentelemetry import context, trace` — `context.attach(ctx)` 把 ctx 设为当前(返回 token);`context.detach(token)` 还原。`trace.get_current_span(ctx).get_span_context()` 从 ctx 取 SpanContext;`.is_valid` 判定是否真 trace。
- 现有 `agent.py` 的 `tracer.start_as_current_span("jiuwenclaw.agent.invoke", ...)` 会以 `context.attach` 设的 remote parent 为父 → 自动成子 span(无需改 agent.py)。
- `patch_method(cls, name, factory)`(本包 `wrap.py`)幂等 + fail-soft;`factory(original)` 返回 wrapped 函数。

**fail-soft-per-class 模式(关键):** `instrument_gateway`/`instrument_agentserver` 对每个目标类:若 `cls is None` 则尝试 lazy-import(生产路径);import 失败(测试环境无 jiuwenclaw)则跳过该类的 patch,不抛。这样测试可只注入 fake 类、生产自动 import 真实类。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/jiuwenswarm_instrumentor/instrumentors/gateway.py` | `channel.request` span(MessageHandler)+ `jiuwenclaw.gateway.agent.request` CLIENT span + traceparent 注入(agent_client) | Create |
| `src/jiuwenswarm_instrumentor/instrumentors/agentserver.py` | `process_message_impl[_stream]` 提取 traceparent + `context.attach` | Create |
| `src/jiuwenswarm_instrumentor/instrumentors/__init__.py` | `apply_instrumentors` 加 gateway + agentserver 步(traces_exporter != none 时) | Modify |
| `tests/instrumentors/test_gateway.py` | gateway 5 用例(fake 类 + CollectingSpanExporter) | Create |
| `tests/instrumentors/test_agentserver.py` | agentserver 3 用例(inject→extract→child 端到端) | Create |
| `tests/instrumentors/test_apply.py` | gateway/agentserver 被调用用例 | Modify |
| `docs/guides/start-jiuwenswarm-with-instrumentor.md` | 验证节加端到端 trace 说明 | Modify |

---

### Task 1: gateway.py — channel.request + CLIENT span + traceparent 注入

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/gateway.py`
- Test: `tests/instrumentors/test_gateway.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/instrumentors/test_gateway.py`:

```python
# tests/instrumentors/test_gateway.py
import pytest
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.gateway import instrument_gateway


class FakeEnvelope:
    def __init__(self, channel_context=None):
        self.channel_context = channel_context


class FakeAgentClient:
    async def send_request(self, envelope):
        return "resp"

    async def send_request_stream(self, envelope):
        yield "chunk1"
        yield "chunk2"


class FakeMessageHandler:
    def __init__(self, ac=None):
        self._ac = ac or FakeAgentClient()

    async def process_stream(self, *args, **kw):
        async for c in self._ac.send_request_stream(FakeEnvelope({})):
            yield c


async def test_send_request_injects_traceparent(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context={})
    result = await FakeAgentClient().send_request(env)
    assert result == "resp"
    assert "traceparent" in env.channel_context
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request"]
    assert len(spans) == 1
    assert spans[0].kind == trace.SpanKind.CLIENT


async def test_send_request_stream_injects_traceparent(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context={})
    chunks = [c async for c in FakeAgentClient().send_request_stream(env)]
    assert chunks == ["chunk1", "chunk2"]
    assert "traceparent" in env.channel_context
    spans = [s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request"]
    assert len(spans) == 1


async def test_inject_failsoft_no_channel_context(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, agent_client_cls=FakeAgentClient)
    env = FakeEnvelope(channel_context=None)
    result = await FakeAgentClient().send_request(env)  # must not raise
    assert result == "resp"


async def test_process_stream_creates_channel_request_span(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, message_handler_cls=FakeMessageHandler, agent_client_cls=FakeAgentClient)
    mh = FakeMessageHandler()
    chunks = [c async for c in mh.process_stream()]
    assert chunks == ["chunk1", "chunk2"]
    spans = [s for s in exporter.spans if s.name == "channel.request"]
    assert len(spans) == 1


async def test_channel_request_is_parent_of_client(exporter):
    tracer = trace.get_tracer("t")
    instrument_gateway(tracer, message_handler_cls=FakeMessageHandler, agent_client_cls=FakeAgentClient)
    mh = FakeMessageHandler()
    _ = [c async for c in mh.process_stream()]
    cr = next(s for s in exporter.spans if s.name == "channel.request")
    cl = next(s for s in exporter.spans if s.name == "jiuwenclaw.gateway.agent.request")
    assert cl.parent is not None
    assert cl.parent.span_id == cr.context.span_id
    assert cl.context.trace_id == cr.context.trace_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_gateway.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.gateway'`

- [ ] **Step 3: 实现** — 创建 `src/jiuwenswarm_instrumentor/instrumentors/gateway.py`:

```python
# src/jiuwenswarm_instrumentor/instrumentors/gateway.py
from __future__ import annotations

from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.wrap import patch_method


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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_gateway.py -v`
Expected: PASS(5 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/gateway.py tests/instrumentors/test_gateway.py
git commit -m "feat: gateway channel.request + CLIENT span + traceparent inject"
```

---

### Task 2: agentserver.py — 提取 traceparent + context.attach

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/agentserver.py`
- Test: `tests/instrumentors/test_agentserver.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/instrumentors/test_agentserver.py`:

```python
# tests/instrumentors/test_agentserver.py
import pytest
from opentelemetry import trace
from opentelemetry.propagate import inject
from jiuwenswarm_instrumentor.instrumentors.agentserver import instrument_agentserver


class FakeRequest:
    def __init__(self, metadata=None):
        self.metadata = metadata


class FakeAdapter:
    """The 'original' process_message_impl simulates agent.invoke by starting a 'child' span."""
    async def process_message_impl(self, request, inputs):
        with trace.get_tracer("t").start_as_current_span("child"):
            pass
        return "ok"

    async def process_message_stream_impl(self, request, inputs):
        with trace.get_tracer("t").start_as_current_span("child"):
            yield "chunk"


async def test_extract_attaches_remote_parent(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    # simulate gateway: create a remote parent span + inject its traceparent into a carrier
    with tracer.start_as_current_span("remote_parent") as remote:
        carrier = {}
        inject(carrier)
    remote_ctx = remote.get_span_context()
    request = FakeRequest(metadata=carrier)
    await FakeAdapter().process_message_impl(request, {})
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.context.trace_id == remote_ctx.trace_id
    assert child.parent is not None
    assert child.parent.span_id == remote_ctx.span_id


async def test_no_metadata_no_attach(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    request = FakeRequest(metadata=None)
    await FakeAdapter().process_message_impl(request, {})
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.parent is None  # root span — no remote parent


async def test_stream_extract_attaches(exporter):
    tracer = trace.get_tracer("t")
    instrument_agentserver(tracer, adapter_cls=FakeAdapter)
    with tracer.start_as_current_span("remote_parent") as remote:
        carrier = {}
        inject(carrier)
    remote_ctx = remote.get_span_context()
    request = FakeRequest(metadata=carrier)
    chunks = [c async for c in FakeAdapter().process_message_stream_impl(request, {})]
    assert chunks == ["chunk"]
    child = next(s for s in exporter.spans if s.name == "child")
    assert child.context.trace_id == remote_ctx.trace_id
    assert child.parent.span_id == remote_ctx.span_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_agentserver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.agentserver'`

- [ ] **Step 3: 实现** — 创建 `src/jiuwenswarm_instrumentor/instrumentors/agentserver.py`:

```python
# src/jiuwenswarm_instrumentor/instrumentors/agentserver.py
from __future__ import annotations

from opentelemetry import context, trace
from opentelemetry.propagate import extract

from jiuwenswarm_instrumentor.wrap import patch_method


def _attach_remote_parent(request):
    """Extract W3C traceparent from request.metadata + attach as current context.
    Returns a detach token (or None if no valid remote parent). Fail-soft.
    Must be called in process_message_impl[_stream] (same task as ReActAgent.invoke)."""
    try:
        carrier = request.metadata if isinstance(request.metadata, dict) else {}
        extracted = extract(carrier)
        remote = trace.get_current_span(extracted).get_span_context()
        if remote is not None and remote.is_valid:
            return context.attach(extracted)
    except Exception:
        pass
    return None


def instrument_agentserver(tracer, *, adapter_cls=None):
    """Wrap JiuWenClawDeepAdapter.process_message_impl[_stream] to extract the gateway's
    traceparent from request.metadata + context.attach it, so the existing agent.invoke span
    (start_as_current_span in agent.py) becomes a child of the gateway CLIENT span.
    NOTE: wrap the _impl methods (not the public process_message) — the public method and
    ReActAgent.invoke are separated by the session-manager queue (task boundary); _impl and
    ReActAgent.invoke share the same task, so context.attach survives.
    Fail-soft: skip if adapter import fails (test env without jiuwenclaw)."""
    if adapter_cls is None:
        try:
            from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
            adapter_cls = JiuWenClawDeepAdapter
        except Exception:
            adapter_cls = None
    if adapter_cls is None:
        return

    def factory_impl(original):
        async def traced(self, request, inputs):
            token = _attach_remote_parent(request)
            try:
                return await original(self, request, inputs)
            finally:
                if token is not None:
                    context.detach(token)
        return traced

    def factory_impl_stream(original):
        async def traced(self, request, inputs):
            token = _attach_remote_parent(request)
            try:
                async for chunk in original(self, request, inputs):
                    yield chunk
            finally:
                if token is not None:
                    context.detach(token)
        return traced

    patch_method(adapter_cls, "process_message_impl", factory_impl)
    patch_method(adapter_cls, "process_message_stream_impl", factory_impl_stream)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_agentserver.py -v`
Expected: PASS(3 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/agentserver.py tests/instrumentors/test_agentserver.py
git commit -m "feat: agentserver extract traceparent from request.metadata + context.attach"
```

---

### Task 3: 接入 apply_instrumentors + 启动指南 + 全量回归

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/__init__.py`
- Modify: `tests/instrumentors/test_apply.py`
- Modify: `docs/guides/start-jiuwenswarm-with-instrumentor.md`

- [ ] **Step 1: 写失败测试** — 在 `tests/instrumentors/test_apply.py` 末尾追加:

```python
def test_apply_instrumentors_calls_gateway_agentserver_when_traces_configured(monkeypatch):
    called = []
    monkeypatch.setattr(
        "jiuwenswarm_instrumentor.instrumentors.gateway.instrument_gateway",
        lambda *a, **k: called.append("gateway"),
    )
    monkeypatch.setattr(
        "jiuwenswarm_instrumentor.instrumentors.agentserver.instrument_agentserver",
        lambda *a, **k: called.append("agentserver"),
    )
    for name in ("llm", "tool", "agent", "session"):
        monkeypatch.setattr(
            f"jiuwenswarm_instrumentor.instrumentors.{name}.instrument_{name}",
            lambda *a, _n=name, **k: None,
        )
    from jiuwenswarm_instrumentor.config import InstrumentorConfig
    from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors
    cfg = InstrumentorConfig(enabled=True, traces_exporter="otlp")  # logs_exporter default "none"
    apply_instrumentors(tracer=object(), meter=Mock(), cfg=cfg)
    assert "gateway" in called
    assert "agentserver" in called
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_apply.py::test_apply_instrumentors_calls_gateway_agentserver_when_traces_configured -v`
Expected: FAIL — `apply_instrumentors` 不调用 `instrument_gateway`/`instrument_agentserver`(`called` 不含 "gateway"/"agentserver")或 `AttributeError: module has no attribute 'gateway'`

- [ ] **Step 3: 实现** — 在 `instrumentors/__init__.py` 的 import 行改为:

```python
from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session, logs, gateway, agentserver
```

在 `apply_instrumentors` 里,现有 logs 条件块**之后**追加(traces 信号级,独立于 logs):

```python
    if getattr(cfg, "traces_exporter", "none") != "none":
        try:
            gateway.instrument_gateway(tracer)
            logger.info("[instrumentor] applied gateway")
        except Exception:
            logger.exception("[instrumentor] failed to apply gateway — skipping")
        try:
            agentserver.instrument_agentserver(tracer)
            logger.info("[instrumentor] applied agentserver")
        except Exception:
            logger.exception("[instrumentor] failed to apply agentserver — skipping")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_apply.py -v`
Expected: PASS(全部,含新 1 例;原 `test_apply_instrumentors_invokes_each` 用 cfg=None,traces_exporter 经 getattr 默认 "none" → 不触发 gateway/agentserver,仍通过)

- [ ] **Step 5: 更新启动指南** — 在 `docs/guides/start-jiuwenswarm-with-instrumentor.md` 的 §3 验证 "应看到的 span" 表里,`jiuwenclaw.agent.invoke` 行**之前**加一行:

```markdown
| `channel.request` | gateway 侧,覆盖消息处理全程;`jiuwenclaw.channel.id`/`jiuwenclaw.request.id`(尽力) |
| `jiuwenclaw.gateway.agent.request` | gateway→agentserver WS 往返(CLIENT);是 agentserver 端 `jiuwenclaw.agent.invoke` 的父 span(端到端 trace 串联) |
```

并在该表后补一句:

```markdown
> 端到端 trace:`channel.request` → `jiuwenclaw.gateway.agent.request`(gateway CLIENT)→ `jiuwenclaw.agent.invoke`(agentserver,经 W3C traceparent 跨 WS 续父)→ `gen_ai.chat`/`gen_ai.tool`。gateway 与 agentserver 的 jiuwenclaw 日志都带同一 trace_id,被 labubu 关联保留(不被 5min 清)。
```

- [ ] **Step 6: 跑全量测试**

Run: `py -3.13 -m pytest`
Expected: PASS(全部既有 + 新增 gateway 5 + agentserver 3 + apply 1 = 9 例)

- [ ] **Step 7: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/__init__.py tests/instrumentors/test_apply.py docs/guides/start-jiuwenswarm-with-instrumentor.md
git commit -m "feat: wire gateway+agentserver trace propagation into apply_instrumentors"
```

---

## Self-Review (已执行)

- **Spec 覆盖**:§4 span 树 → Task 1(channel.request + CLIENT)+ Task 2(agent.invoke 成子,经 attach);§5.1 gateway → Task 1;§5.2 agentserver → Task 2;§5.3 wiring → Task 3;§7 fail-soft → Task 1/2(per-class + _inject/_attach try/except);§8 测试 → Task 1(5 例)+ Task 2(3 例);§9 验收 → Task 3 全量回归 + 启动指南。无遗漏。
- **Placeholder 扫描**:无 TBD/TODO;每步含完整代码 + 命令。
- **类型一致**:`instrument_gateway(tracer, *, message_handler_cls=None, agent_client_cls=None)` 在 Task 1 定义、Task 3 `apply_instrumentors` 调用 `gateway.instrument_gateway(tracer)` 一致;`instrument_agentserver(tracer, *, adapter_cls=None)` 在 Task 2 定义、Task 3 调用一致;`_inject_traceparent`/`_attach_remote_parent`/`_envelope_attrs`/`_process_attrs` 前后一致;span 名 `channel.request` / `jiuwenclaw.gateway.agent.request` 前后一致。
- **API 校准**:`inject`/`extract` from `opentelemetry.propagate`;`context.attach/detach` + `trace.get_current_span` from `opentelemetry`;`SpanKind` from `opentelemetry.trace`;`patch_method` 本包。fail-soft-per-class 模式确保测试环境(无 jiuwenclaw)可注入 fake 类、生产自动 import 真实类。
