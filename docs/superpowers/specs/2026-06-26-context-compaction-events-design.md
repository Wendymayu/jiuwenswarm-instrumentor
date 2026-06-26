# jiuwenswarm-instrumentor 上下文压缩事件 设计规格

- **日期**: 2026-06-26
- **状态**: Draft — 待用户评审
- **参考**: openjiuwen `ContextEngine` + `SessionModelContext`(`context_engine/context.py`)+ 9 个 processor(`processor/offloader/*`、`processor/compressor/*`)。旧 `jiuwenclaw/telemetry` **不**观测压缩(只做静态 composition,我们已镜像),本特性无先例,为新观测。
- **相关**: `docs/superpowers/specs/2026-06-26-context-token-attribution-design.md`(静态 token 归因,本特性是动态压缩事件,互补)。

---

## 1. 背景与目标

静态 token 归因(已落地)记录 LLM 调用**最终**上下文的组成(skill/system/user/...各占多少)。但它看不到**动态压缩过程**——openjiuwen 的 ContextEngine 在每轮 LLM 调用前(GET 路径)和每次工具执行后(ADD 路径)会跑 processor 链,offload/摘要/清空/去重消息,token 实打实减少。当前这些压缩**完全不可见**。

本规格采集**上下文压缩事件**:何时压缩、哪个 processor 压的、压前/压后 token、省了多少。回答"上下文为什么缩了/被谁砍了/压力多大"。

### 关键约束
- **自包含,不依赖 `jiuwenclaw.telemetry`**(旧 rail 不观测压缩,本特性无先例)。允许只读 openjiuwen 的 `SessionModelContext` + 3 个 GET processor 类(引擎类,非 telemetry)。
- **非侵入**:纯 monkey-patch,不改 openjiuwen/jiuwenclaw 源码。
- **零新依赖**:复用 openjiuwen 的 `context.token_counter()`(引擎自己的 TiktokenCounter)做 token 计数,与引擎账本一致。
- **fail-soft**:压缩观测失败不影响应用 + 不吞 `IrreducibleContextError`。

---

## 2. 范围

### 做
- **ADD 路径(整体)**:wrap `SessionModelContext.add_messages` → buffer 前后 token delta = **NET(净)delta**(压缩移除 − 新增追加;`add_messages` 在 processor 链后 `add_back` 新消息,故 delta 非纯压缩,当新增>压缩时 `after>=before` 会漏掉压缩事件。**已知限制**,GET 路径不受影响、是 gross)。一个事件/次 `add_messages` 调用。
- **GET 路径(per-processor)**:wrap 3 个 GET processor(`FullCompactProcessor`/`RoundLevelCompressor`/`ToolResultDedupProcessor`)的 `on_get_context_window` → 该 processor 压前/压后 window token delta = 纯压缩。一个事件/processor 触发。
- 每次真压缩(tokens_saved>0)出 `context.compaction` span + 2 metric。
- ADD 整体无 processor_type(GET 才有,因 GET per-processor)。

### 不做(非目标)
- 不 wrap ADD 的 7 个 processor per-processor(ADD 用整体 A,够答"压缩发生+省多少";哪个 ADD processor 是 v2)。
- 不 wrap `get_context_window` 整体(GET 整体 delta 混截断,无意义;GET 用 per-processor 精确)。
- 不采集 trigger_reason(threshold/force/overflow;需读 processor 内部状态,per-processor v2 可加)。
- 不采集"删了哪些消息"(ContextEvent.messages_to_modify 索引;只记 messages_before/after 计数)。
- 不替代静态 token 归因(两者互补:静态看最终组成,本特性看压缩过程)。

---

## 3. 设计原则

- **ADD 整体 + GET per-processor(混合)**:ADD 改 buffer(前后快照是 **NET delta**,非纯压缩 — 已知限制,见 §2);GET 改 window 副本(wrapper 够不着压前 window,只能 per-processor 拿该 processor 压前/压后,是 **gross**)。
- **只在真压缩时出**:tokens_saved>0 才出 span/metric,零压缩零开销。
- **用引擎的 token_counter**:`context.token_counter().count_messages(...)`,和引擎账本一致(不用我们自己的 `_get_token_counter`,避免 mismatch)。
- **span 挂 agent.invoke 下**:压缩在 ReAct 循环内、gen_ai.chat 之外;agent.invoke 是 current → `context.compaction` 作其子 span(经 OTel current context 自动嵌套)。不挂 gen_ai.chat(压缩时它不 active)。
- **fail-soft + IrreducibleContextError 透传**:wrap/计数/emit 全 try/except;`IrreducibleContextError` 透传(真错误,不吞)。

---

## 4. Span + 事件

```
jiuwenclaw.agent.invoke
├─ context.compaction (ADD)                  ← add_messages 整体压缩
├─ context.compaction (GET, FullCompact)     ← per-processor
├─ context.compaction (GET, RoundLevelCompressor)  ← per-processor(同轮可多个)
├─ gen_ai.chat
└─ gen_ai.tool
```

`context.compaction` span(INTERNAL)属性:
- `context.compaction.path` = `ADD` | `GET`
- `context.compaction.processor_type` = GET 时 `FullCompactProcessor`/`RoundLevelCompressor`/`ToolResultDedupProcessor`;ADD 时空串
- `context.compaction.tokens_before` / `tokens_after` / `tokens_saved`(= before - after)
- `context.compaction.messages_before` / `messages_after`
- `jiuwenclaw.session.id` / `jiuwenclaw.context.id`(trace 关联)

+ 2 metric(只在 tokens_saved>0 时记):
- `gen_ai.context.compaction.count`(Counter,labels `path`+`processor_type`)—— 压缩次数
- `gen_ai.context.compaction.tokens_saved`(Histogram,labels `path`+`processor_type`)—— 每次省的 token

---

## 5. 架构 + 数据流

### 5.1 ADD 路径(整体)— wrap `SessionModelContext.add_messages`

```
traced_add_messages(self, messages_to_add, **kw):
    before_tokens = _count(self, self.get_messages())     # buffer 前
    before_msgs = len(self.get_messages())
    result = await original(self, messages_to_add, **kw)  # ADD processor 链原地改 buffer
    after_tokens = _count(self, self.get_messages())      # buffer 后
    after_msgs = len(self.get_messages())
    if after_tokens < before_tokens:                       # 真压缩
        _emit(tracer, metrics, self, path="ADD", processor_type="",
              before_tokens, after_tokens, before_msgs, after_msgs)
    return result
```

### 5.2 GET 路径(per-processor)— wrap 3 个 GET processor 的 `on_get_context_window`

```
traced_on_get_context_window(self, context, window, **kw):
    before_tokens = _count(context, _window_messages(window))   # 该 processor 压前 window
    before_msgs = len(_window_messages(window))
    event, window = await original.on_get_context_window(context, window, **kw)  # 该 processor 压缩
    after_tokens = _count(context, _window_messages(window))   # 压后 window
    after_msgs = len(_window_messages(window))
    if after_tokens < before_tokens:
        _emit(tracer, metrics, context, path="GET", processor_type=self.processor_type(),
              before_tokens, after_tokens, before_msgs, after_msgs)
    return event, window
```

3 个 GET processor:`FullCompactProcessor`、`RoundLevelCompressor`、`ToolResultDedupProcessor`(都实现 `on_get_context_window`,signature `(self, context, window, **kw) -> (ContextEvent|None, ContextWindow)`)。

### 5.3 helpers

```python
def _count(context, messages):
    """用引擎的 token_counter 计数。fail-soft → 0。"""
    try:
        return context.token_counter().count_messages(messages or [])
    except Exception:
        return 0

def _window_messages(window):
    """从 ContextWindow 取消息列表。适配 get_messages() / .messages / statistic。fail-soft → []。"""
    try:
        if window is None:
            return []
        if hasattr(window, "get_messages"):
            return window.get_messages() or []
        if hasattr(window, "messages"):
            return window.messages or []
        if hasattr(window, "statistic") and hasattr(window.statistic, "total_messages"):
            return []  # 只有计数无列表 → 返 [],计数靠 _count(0)
        return list(window) if isinstance(window, (list, tuple)) else []
    except Exception:
        return []

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
            pass  # span 仅记属性,无额外工作
        mattrs = {A.GEN_AI_CONTEXT_COMPACTION_PATH: path,
                  A.GEN_AI_CONTEXT_COMPACTION_PROCESSOR_TYPE: processor_type,
                  A.GEN_AI_SYSTEM: "jiuwenclaw"}
        metrics.record_context_compaction(1, saved, mattrs)
    except Exception:
        logger.debug("[instrumentor] context compaction emit failed", exc_info=True)
```

`_sid(context)`/`_cid(context)`:`context.session_id()`/`context.context_id()`,fail-soft → ""。

---

## 6. 组件设计

### 6.1 `instrumentors/context_compaction.py`(新建)

```python
def instrument_context_compaction(tracer, metrics, *, session_context_cls=None, processor_classes=None):
    """wrap SessionModelContext.add_messages (ADD 整体) + 3 个 GET processor 的 on_get_context_window
    (per-processor)。每次真压缩出 context.compaction span + 2 metric。fail-soft per target。"""
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
                bt, bm = _count(self, self.get_messages()), len(self.get_messages())
                try:
                    result = await original(self, messages_to_add, **kw)
                except Exception:
                    raise  # IrreducibleContextError 等透传
                at, am = _count(self, self.get_messages()), len(self.get_messages())
                if at < bt:
                    _emit(tracer, metrics, self, path="ADD", processor_type="",
                          before_tokens=bt, after_tokens=at, before_msgs=bm, after_msgs=am)
                return result
            return traced
        patch_method(session_context_cls, "add_messages", factory_add)

    # --- GET per-processor ---
    if processor_classes is None:
        processor_classes = []
        for _name, _path in (("FullCompactProcessor", "openjiuwen.core.context_engine.processor.compressor.full_compact_processor"),
                             ("RoundLevelCompressor", "openjiuwen.core.context_engine.processor.compressor.round_level_compressor"),
                             ("ToolResultDedupProcessor", "openjiuwen.core.context_engine.processor.compressor.tool_result_dedup_processor")):
            try:
                _m = __import__(_path, fromlist=[_name])
                processor_classes.append(getattr(_m, _name))
            except Exception:
                pass
    for cls in processor_classes:
        def factory_get(original):  # noqa: B023
            async def traced(self, context, window, **kw):
                bt, bm = _count(context, _window_messages(window)), len(_window_messages(window))
                try:
                    event, window = await original(context, context, window, **kw)
                except Exception:
                    raise
                at, am = _count(context, _window_messages(window)), len(_window_messages(window))
                if at < bt:
                    ptype = ""
                    try: ptype = self.processor_type()
                    except Exception: pass
                    _emit(tracer, metrics, context, path="GET", processor_type=ptype,
                          before_tokens=bt, after_tokens=at, before_msgs=bm, after_msgs=am)
                return event, window
            return traced
        patch_method(cls, "on_get_context_window", factory_get)
```

> 注:GET per-processor 的 `on_get_context_window(self, context, window, **kw)` —— openjiuwen 的 processor 是实例方法,patch 在类上;`self` 是 processor 实例,`context` 是 SessionModelContext。`self.processor_type()` 取 processor 类型名。impl 时确认 signature(`on_get_context_window` 可能是 `(self, context, context_window, **kwargs)` —— 参数名 `context_window`)。

### 6.2 `attributes.py`

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

### 6.3 `metrics.py`

```python
self._context_compaction_count = meter.create_counter(
    "gen_ai.context.compaction.count", unit="{event}",
    description="Context compaction episodes, by path+processor_type",
)
self._context_compaction_tokens_saved = meter.create_histogram(
    "gen_ai.context.compaction.tokens_saved", unit="{token}",
    description="Tokens saved per context compaction, by path+processor_type",
)

def record_context_compaction(self, count, tokens_saved, attrs):
    try:
        self._context_compaction_count.add(int(count or 0), attrs)
        self._context_compaction_tokens_saved.record(int(tokens_saved or 0), attrs)
    except Exception:
        logger.debug("[instrumentor] context compaction metric failed", exc_info=True)
```

### 6.4 `instrumentors/__init__.py` + `activate.py`

`apply_instrumentors` 加一步(`cfg.traces_exporter != "none"` 时):

```python
    if getattr(cfg, "traces_exporter", "none") != "none":
        try:
            context_compaction.instrument_context_compaction(tracer, metrics)
            logger.info("[instrumentor] applied context_compaction")
        except Exception:
            logger.exception("[instrumentor] failed to apply context_compaction — skipping")
```

(`metrics` 是 `apply_instrumentors` 里的 `Metrics(meter)` 实例。)

---

## 7. 错误处理

- 全 fail-soft:wrap/计数/emit 全 try/except → 不抛、不影响应用。
- `IrreducibleContextError` 透传(`except Exception: raise`,不吞)。
- 只在真压缩时出(`after_tokens < before_tokens`),零压缩零开销。
- `context.token_counter()` 失败 → `_count` 返 0 → 无 delta → 不出。
- `_window_messages` 适配失败 → 返 [] → 计数 0 → 不出。
- GET per-processor import 失败(openjiuwen 布局变)→ 跳过该 processor(fail-soft per target)。
- `SessionModelContext` import 失败 → 跳过 ADD wrap。

---

## 8. 测试(`tests/instrumentors/test_context_compaction.py` 新建)

fake `SessionModelContext`(token_counter + get_messages + session_id/context_id)+ fake GET processor(on_get_context_window 改 window)+ `CollectingSpanExporter` + fake metrics:

```python
class FakeCounter:
    def count_messages(self, msgs): return sum(len(getattr(m, "content", "") or "") for m in msgs)  # len-based
class FakeSessionContext:
    def __init__(self, messages): self._msgs = messages; self._sid="s1"; self._cid="c1"
    def get_messages(self): return self._msgs
    def token_counter(self): return FakeCounter()
    def session_id(self): return self._sid
    def context_id(self): return self._cid
class FakeWindow:
    def __init__(self, messages): self.messages = messages
class FakeGetProcessor:
    def processor_type(self): return "FakeGetProcessor"
    async def on_get_context_window(self, context, window, **kw):
        # 模拟压缩:砍掉一半消息
        window.messages = window.messages[:len(window.messages)//2]
        return None, window
```

用例:
1. `test_add_compaction_emits_span_metric` — `add_messages` 后 buffer 缩 → span(path=ADD)+ metric。
2. `test_add_no_compaction_no_emit` — buffer 不变 → 不出 span。
3. `test_get_per_processor_compaction` — `on_get_context_window` 缩 window → span(path=GET, processor_type=FakeGetProcessor)+ metric。
4. `test_get_no_compaction_no_emit` — window 不变 → 不出。
5. `test_multiple_get_processors` — 两个 processor 都缩 → 两个 span(各自 processor_type)。
6. `test_attributes_correct` — span 上 tokens_before/after/saved、path、processor_type、session_id、context_id。
7. `test_failsoft_counter_raises` — token_counter 抛 → 不出、不崩。
8. `test_irreducible_propagates` — `on_get_context_window` 抛 `IrreducibleContextError`(或任意异常)→ 透传,不吞。

---

## 9. 验收

- `OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp` 跑一条带压缩的对话(长上下文/多次工具),labubu 的 `jiuwenclaw.agent.invoke` trace 下出现 `context.compaction` span(path=ADD/GET、processor_type、tokens_before/after/saved)。
- `gen_ai.context.compaction.count` / `gen_ai.context.compaction.tokens_saved` metric 在 labubu(按 path/processor_type)。
- 无压缩的请求 → 无 `context.compaction` span(零压缩零开销)。
- 关 `OTEL_TRACES_EXPORTER` → 无 span;`OTEL_ENABLED=false` → 全 no-op。
- 单测全绿(新增 8 例)。
