# 上下文压缩事件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 采集 openjiuwen ContextEngine 的上下文压缩事件(ADD 整体 + GET per-processor),每次真压缩出 `context.compaction` span + 2 metric。

**Architecture:** 新建 `instrumentors/context_compaction.py`,wrap `SessionModelContext.add_messages`(ADD 整体,buffer 前后 delta=纯压缩)+ 3 个 GET processor(`FullCompactProcessor`/`RoundLevelCompressor`/`ToolResultDedupProcessor`)的 `on_get_context_window`(per-processor,window 前后 delta=纯压缩)。用引擎的 `context.token_counter()` 计数。只在 tokens_saved>0 时出 span/metric。零 openjiuwen/jiuwenclaw 源码改动;fail-soft。

**Tech Stack:** Python 3.13(`py -3.13`)、opentelemetry-sdk、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-26-context-compaction-events-design.md`

**关键参考(实测):**
- `SessionModelContext`:`openjiuwen/core/context_engine/context/context.py:41`。`add_messages(self, messages_to_add, **kwargs)`(:165)、`get_messages()`(:229)、`token_counter()`(:515)、`session_id()`(:108)、`context_id()`(:111)。
- GET processor 基类:`openjiuwen/core/context_engine/processor/base.py:38`(`ContextProcessor`)。`on_get_context_window(self, context, context_window, **kwargs) -> (ContextEvent|None, ContextWindow)`(:71-103)。`processor_type()`(:161)。
- 3 个 GET processor:`FullCompactProcessor`(`processor/compressor/full_compact_processor.py`)、`RoundLevelCompressor`(`processor/compressor/round_level_compressor.py`)、`ToolResultDedupProcessor`(`processor/compressor/tool_result_dedup_processor.py`)。
- `patch_method(cls, name, factory)`(本包 `wrap.py`):`factory(original)` 返回 wrapped 函数,`original` 是未绑定函数,调用 `original(self, ...)`。

**与 spec 的修正:** spec §5.2 的 `original.on_get_context_window(context, context, window, **kw)` 有误;正确是 `original(self, context, context_window, **kw)`(original 是未绑定函数,self 是 processor 实例)。本 plan 用正确签名。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/jiuwenswarm_instrumentor/attributes.py` | `GEN_AI_CONTEXT_COMPACTION_*`(7)+ `JIUWENCLAW_CONTEXT_ID` 常量 | Modify |
| `src/jiuwenswarm_instrumentor/metrics.py` | `gen_ai.context.compaction.count` Counter + `gen_ai.context.compaction.tokens_saved` Histogram + record 方法 | Modify |
| `src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py` | `instrument_context_compaction` + helpers(`_count`/`_window_messages`/`_emit`/`_sid`/`_cid`)+ ADD wrap + GET per-processor wrap | Create |
| `src/jiuwenswarm_instrumentor/instrumentors/__init__.py` | `apply_instrumentors` 加 context_compaction 步 | Modify |
| `tests/test_metrics.py` | 2 个新 metric 的 fail-soft 测试 | Modify |
| `tests/instrumentors/test_context_compaction.py` | 8 用例(fake SessionContext + fake GET processor + fake window) | Create |

---

### Task 1: attributes + metrics — 8 常量 + 2 instrument

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/attributes.py`
- Modify: `src/jiuwenswarm_instrumentor/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/test_metrics.py` 末尾追加:

```python
def test_context_compaction_counters_created():
    from unittest.mock import Mock
    meter = Mock()
    Metrics(meter)
    names = [c.args[0] for c in meter.create_counter.call_args_list]
    hists = [h.args[0] for h in meter.create_histogram.call_args_list]
    assert "gen_ai.context.compaction.count" in names
    assert "gen_ai.context.compaction.tokens_saved" in hists


def test_context_compaction_record_failsoft():
    from unittest.mock import Mock
    meter = Mock()
    m = Metrics(meter)
    m._context_compaction_count.add.side_effect = RuntimeError("boom")
    m._context_compaction_tokens_saved.record.side_effect = RuntimeError("boom")
    m.record_context_compaction(1, 50, {"context.compaction.path": "ADD"})  # must not raise
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/test_metrics.py::test_context_compaction_counters_created -v`
Expected: FAIL — `AttributeError: 'Metrics' object has no attribute '_context_compaction_count'`

- [ ] **Step 3: 实现** — 在 `attributes.py` 末尾(jiuwenclaw 维度区,`JIUWENCLAW_DOMAIN_ID` 之后)加:

```python
GEN_AI_CONTEXT_COMPACTION_PATH = "context.compaction.path"
GEN_AI_CONTEXT_COMPACTION_PROCESSOR_TYPE = "context.compaction.processor_type"
GEN_AI_CONTEXT_COMPACTION_TOKENS_BEFORE = "context.compaction.tokens_before"
GEN_AI_CONTEXT_COMPACTION_TOKENS_AFTER = "context.compaction.tokens_after"
GEN_AI_CONTEXT_COMPACTION_TOKENS_SAVED = "context.compaction.tokens_saved"
GEN_AI_CONTEXT_COMPACTION_MESSAGES_BEFORE = "context.compaction.messages_before"
GEN_AI_CONTEXT_COMPACTION_MESSAGES_AFTER = "context.compaction.messages_after"
JIUWENCLAW_CONTEXT_ID = "jiuwenclaw.context.id"
```

在 `metrics.py` 的 `Metrics.__init__` 末尾(最后一个 counter 之后)加:

```python
        self._context_compaction_count = meter.create_counter(
            "gen_ai.context.compaction.count", unit="{event}",
            description="Context compaction episodes, by path+processor_type",
        )
        self._context_compaction_tokens_saved = meter.create_histogram(
            "gen_ai.context.compaction.tokens_saved", unit="{token}",
            description="Tokens saved per context compaction, by path+processor_type",
        )
```

在 `Metrics` 类末尾(最后一个 record 方法之后)加:

```python
    def record_context_compaction(self, count, tokens_saved, attrs):
        try:
            self._context_compaction_count.add(int(count or 0), attrs)
            self._context_compaction_tokens_saved.record(int(tokens_saved or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] context compaction metric failed", exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/test_metrics.py -v`
Expected: PASS（全部，含新 2 例）

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/attributes.py src/jiuwenswarm_instrumentor/metrics.py tests/test_metrics.py
git commit -m "feat: gen_ai.context.compaction attrs + count/tokens_saved metrics"
```

---

### Task 2: context_compaction.py — ADD 整体 + GET per-processor wrap

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py`
- Test: `tests/instrumentors/test_context_compaction.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/instrumentors/test_context_compaction.py`:

```python
# tests/instrumentors/test_context_compaction.py
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.context_compaction import instrument_context_compaction


class FakeMsg:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeCounter:
    def count_messages(self, msgs):
        return sum(len(getattr(m, "content", "") or "") for m in (msgs or []))


class FakeSessionContext:
    def __init__(self, messages=None):
        self._msgs = messages or []
    def get_messages(self):
        return self._msgs
    def token_counter(self):
        return FakeCounter()
    def session_id(self):
        return "s1"
    def context_id(self):
        return "c1"


class FakeMetrics:
    def __init__(self):
        self.calls = []
    def record_context_compaction(self, count, tokens_saved, attrs):
        self.calls.append({"count": count, "tokens_saved": tokens_saved, "attrs": attrs})


class FakeWindow:
    def __init__(self, messages):
        self.messages = messages


class FakeGetProcessor:
    def processor_type(self):
        return "FakeGetProcessor"
    async def on_get_context_window(self, context, context_window, **kw):
        # 模拟压缩:砍掉一半消息
        half = len(context_window.messages) // 2
        context_window.messages = context_window.messages[:half]
        return None, context_window


class FakeGetProcessorNoOp:
    def processor_type(self):
        return "FakeGetProcessorNoOp"
    async def on_get_context_window(self, context, context_window, **kw):
        return None, context_window  # 不压缩


async def test_add_compaction_emits_span_metric(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            self._msgs = self._msgs[:len(self._msgs) // 2]  # 模拟压缩
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await sc.add_messages([])
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1
    assert spans[0].attributes["context.compaction.path"] == "ADD"
    assert spans[0].attributes["context.compaction.tokens_saved"] > 0
    assert spans[0].attributes["jiuwenclaw.session.id"] == "s1"
    assert len(fm.calls) == 1
    assert fm.calls[0]["attrs"]["context.compaction.path"] == "ADD"


async def test_add_no_compaction_no_emit(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            pass  # buffer 不变
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello")])
    await sc.add_messages([])
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0
    assert len(fm.calls) == 0


async def test_get_per_processor_compaction(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessor])
    proc = FakeGetProcessor()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await proc.on_get_context_window(ctx, window)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1
    assert spans[0].attributes["context.compaction.path"] == "GET"
    assert spans[0].attributes["context.compaction.processor_type"] == "FakeGetProcessor"
    assert spans[0].attributes["context.compaction.tokens_saved"] > 0
    assert len(fm.calls) == 1


async def test_get_no_compaction_no_emit(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessorNoOp])
    proc = FakeGetProcessorNoOp()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "hello")])
    await proc.on_get_context_window(ctx, window)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0
    assert len(fm.calls) == 0


async def test_multiple_get_processors(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None,
                                  processor_classes=[FakeGetProcessor, FakeGetProcessorNoOp])
    ctx = FakeSessionContext()
    w1 = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await FakeGetProcessor().on_get_context_window(ctx, w1)
    w2 = FakeWindow([FakeMsg("tool", "hello"), FakeMsg("tool", "worldxx")])
    await FakeGetProcessorNoOp().on_get_context_window(ctx, w2)
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 1  # only the compressing one
    assert spans[0].attributes["context.compaction.processor_type"] == "FakeGetProcessor"


async def test_attributes_correct(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    instrument_context_compaction(tracer, fm, session_context_cls=None, processor_classes=[FakeGetProcessor])
    proc = FakeGetProcessor()
    ctx = FakeSessionContext()
    window = FakeWindow([FakeMsg("tool", "abcdefgh"), FakeMsg("tool", "xxxxxxxx")])  # 16 tokens
    await proc.on_get_context_window(ctx, window)
    sp = [s for s in exporter.spans if s.name == "context.compaction"][0]
    assert sp.attributes["context.compaction.tokens_before"] == 16
    assert sp.attributes["context.compaction.tokens_after"] == 8  # half
    assert sp.attributes["context.compaction.tokens_saved"] == 8
    assert sp.attributes["context.compaction.messages_before"] == 2
    assert sp.attributes["context.compaction.messages_after"] == 1
    assert sp.attributes["jiuwenclaw.context.id"] == "c1"


async def test_failsoft_counter_raises(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class _BoomCtx(FakeSessionContext):
        def token_counter(self):
            raise RuntimeError("boom")
    class FakeSC(_BoomCtx):
        async def add_messages(self, messages_to_add, **kw):
            self._msgs = self._msgs[:len(self._msgs) // 2]
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello"), FakeMsg("tool", "world")])
    await sc.add_messages([])  # must not raise
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0  # counter failed → no delta → no emit


async def test_irreducible_propagates(exporter):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    class IrreducibleContextError(Exception):
        pass
    class FakeSC(FakeSessionContext):
        async def add_messages(self, messages_to_add, **kw):
            raise IrreducibleContextError("cannot compact")
    instrument_context_compaction(tracer, fm, session_context_cls=FakeSC, processor_classes=[])
    sc = FakeSC([FakeMsg("tool", "hello")])
    raised = False
    try:
        await sc.add_messages([])
    except IrreducibleContextError:
        raised = True
    assert raised  # 透传,不吞
    spans = [s for s in exporter.spans if s.name == "context.compaction"]
    assert len(spans) == 0  # 异常 → 不 emit
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_compaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.context_compaction'`

- [ ] **Step 3: 实现** — 创建 `src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py`:

```python
# src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py
from __future__ import annotations
import logging

from opentelemetry.trace import SpanKind

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
        with tracer.start_as_current_span("context.compaction", kind=SpanKind.INTERNAL, attributes=attrs):
            pass
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
            async def traced(self, messages_to_add, **kw):
                bt = _count(self, self.get_messages())
                bm = len(self.get_messages() or [])
                result = await original(self, messages_to_add, **kw)  # IrreducibleContextError 透传
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_compaction.py -v`
Expected: PASS(8 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/context_compaction.py tests/instrumentors/test_context_compaction.py
git commit -m "feat: context_compaction — ADD overall + GET per-processor compaction events"
```

---

### Task 3: 接入 apply_instrumentors + 全量回归

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/__init__.py`

- [ ] **Step 1: 实现** — 在 `instrumentors/__init__.py` 的 import 行加 `context_compaction`:

```python
from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session, logs, gateway, agentserver, context_compaction
```

在 `apply_instrumentors` 里,现有 gateway/agentserver 块**之后**追加(traces 信号级):

```python
    if getattr(cfg, "traces_exporter", "none") != "none":
        try:
            context_compaction.instrument_context_compaction(tracer, metrics)
            logger.info("[instrumentor] applied context_compaction")
        except Exception:
            logger.exception("[instrumentor] failed to apply context_compaction — skipping")
```

> 注:如果 `apply_instrumentors` 已有一个 `if getattr(cfg, "traces_exporter", "none") != "none":` 块(装 gateway + agentserver),把 context_compaction 加到**同一个块里**(gateway/agentserver 之后),不要开新块。读文件确认。

- [ ] **Step 2: 跑全量测试**

Run: `py -3.13 -m pytest`
Expected: PASS(全部既有 + Task1 2 + Task2 8 = 10 新;总数应为 86)

- [ ] **Step 3: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/__init__.py
git commit -m "feat: wire context_compaction into apply_instrumentors"
```

---

## Self-Review (已执行)

- **Spec 覆盖**:§4 span+事件 → Task 2 `_emit`;§5.1 ADD wrap → Task 2 `factory_add`;§5.2 GET per-processor → Task 2 `factory_get`;§5.3 helpers → Task 2;§6.1 context_compaction.py → Task 2;§6.2 attributes → Task 1;§6.3 metrics → Task 1;§6.4 wiring → Task 3;§7 fail-soft + IrreducibleContextError 透传 → Task 2(try/except + `raise` 透传);§8 测试 8 例 → Task 2;§9 验收 → Task 3 全量回归。无遗漏。
- **Placeholder 扫描**:无 TBD/TODO;每步含完整代码 + 命令。
- **类型一致**:`instrument_context_compaction(tracer, metrics, *, session_context_cls=None, processor_classes=None)` 在 Task 2 定义、Task 3 调用 `context_compaction.instrument_context_compaction(tracer, metrics)` 一致;`record_context_compaction(count, tokens_saved, attrs)` 在 Task 1 定义、Task 2 `_emit` 调用一致;属性常量 `GEN_AI_CONTEXT_COMPACTION_*` + `JIUWENCLAW_CONTEXT_ID` 在 Task 1 定义、Task 2 使用一致;GET wrap 签名 `on_get_context_window(self, context, context_window, **kw)` + `original(self, context, context_window, **kw)` 一致(spec §5.2 的 bug 已修正)。
- **API 校准**:`SessionModelContext.add_messages`/`get_messages`/`token_counter`/`session_id`/`context_id` 路径来自实测;GET processor `on_get_context_window(self, context, context_window, **kw)` + `processor_type()` 来自 base.py;`patch_method` 调 `original(self, ...)` 与现有 instrumentor 一致。
