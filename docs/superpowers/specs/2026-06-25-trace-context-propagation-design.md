# jiuwenswarm-instrumentor 端到端 trace 上下文传播 设计规格

- **日期**: 2026-06-25
- **状态**: Draft — 待用户评审
- **参考**: 旧 `jiuwenclaw/telemetry/` 的传播机制(`entry.py` / `agent_client_proxy.py` / `telemetry_rail.py:301-430`,运行时**不依赖**,仅作参照);[W3C TraceContext](https://www.w3.org/TR/trace-context/);OTel Python `opentelemetry.propagate.inject/extract` + `context.attach/detach`。
- **相关**: `docs/troubleshooting/logs-dropped-by-app-routing-filters.md`(日志侧);`docs/superpowers/specs/2026-06-25-log-collection-design.md`(logs 信号)。

---

## 1. 背景与目标

logs 信号已落地,但 gateway 与 agentserver 是两个进程,二者的 trace **不互通**:gateway 的 `jiuwenclaw.gateway.*` 日志 `trace_id=0`(消息处理不在任何 span 里)→ labubu 每 5min 清掉;agentserver 的 `agent.invoke` trace 是独立新 trace,与 gateway 对不上。结果是**边界问题(丢请求/丢响应/慢在哪)事后无法定位**,且 gateway 日志 5min 后消失。

本规格实现 **W3C TraceContext 跨 WS 边界传播**,把 gateway 与 agentserver 串成**一条端到端 trace**,并让两边的 jiuwenclaw 日志都带同一 `trace_id`(被 labubu 关联保留,不被 5min 清)。

### 关键约束
- **自包含,不依赖 `jiuwenclaw.telemetry`**(沿用本包原则)。允许 fail-soft 引用 `jiuwenclaw.gateway.agent_client.WebSocketAgentServerClient`、`jiuwenclaw.gateway.message_handler.MessageHandler`、`jiuwenclaw.agentserver.deep_agent.interface_deep.JiuWenClawDeepAdapter`(应用类,非 telemetry)。
- **非侵入**:纯 monkey-patch,不改 jiuwenclaw 源码。carrier 复用 E2A 既有通用字段(`channel_context` / `metadata`),不动 E2A codec。
- **零新依赖**:用 `opentelemetry.propagate`(api 层,已在)。
- **不改现有 `agent.py`**:`agent.invoke` span 经 `context.attach` **自动**成子 span(Approach 1)。

---

## 2. 范围

### 做
- **gateway 注入**:wrap `MessageHandler.process_message` + `process_stream` → `channel.request` span(全程 current,关联所有 gateway 日志);wrap `WebSocketAgentServerClient.send_request` + `send_request_stream` → `jiuwenclaw.gateway.agent.request`(CLIENT)span + 注入 traceparent 进 `envelope.channel_context`。
- **agentserver 提取**:wrap `JiuWenClawDeepAdapter.process_message_impl` + `process_message_stream_impl` → 从 `request.metadata` 提取 traceparent → `context.attach` → 现有 `agent.invoke` 自动成子 span。
- unary + streaming 两路都覆盖。
- fail-soft:无 traceparent 时退化为今天的独立 trace(agent.invoke 为根)。

### 不做(非目标)
- 不动 E2A wire codec(carrier 是通用 `channel_context`/`metadata`,codec 不感知 trace)。
- 不改 `agent.py` / `llm.py` / `tool.py`(现有 span 自动嵌套)。
- 不做 `JiuWenClaw.create_instance`(session.create)的 parent 续接——它在 task 边界之前的另一 task,频率低(每 session 一次),留作独立 span,可接受。
- 不做 file-transfer / 自定义 WS handler 路径的 trace 续接(它们 `metadata=None`,无 traceparent → 退化为独立 trace)。

---

## 3. 设计原则

- **复用旧机制**:carrier(W3C `traceparent` in `channel_context`→`metadata`)与 inject/extract 时机与旧 telemetry 一致,但用 monkey-patch 替代其源码 hook。
- **attach 而非 ContextVar**:`process_message_impl` 与 `ReActAgent.invoke` 同 task(Task C,session manager 队列之后),`context.attach` 能透传;无需 ContextVar + 改 agent.py。
- **fail-soft**:inject/extract/attach/detach 全 try/except,绝不阻断应用。
- **可关停**:`OTEL_ENABLED`(总)+ `OTEL_TRACES_EXPORTER`(trace 信号)。

---

## 4. Span 树(端到端)

```
channel.request (gateway, INTERNAL)              ← 新增,wrap MessageHandler.process_message[_stream]
└─ jiuwenclaw.gateway.agent.request (gateway, CLIENT)  ← 新增,wrap send_request[_stream],注入 traceparent
   └─ jiuwenclaw.agent.invoke (agentserver, INTERNAL)  ← 现有,经 context.attach 自动成子 span
      ├─ gen_ai.chat (现有)
      └─ gen_ai.tool (现有)
```

- `channel.request` 在 gateway 全程消息处理期间 current → `jiuwenclaw.gateway.*` 日志(channel_manager/message_handler/agent_client)全带 trace_id → **不被 labubu 5min 清**。
- `gateway.agent.request`(CLIENT)是 WS 往返 span;注入的 `traceparent` 指向它 → agentserver 续接它为父。
- `agent.invoke`(现有 `start_as_current_span`)以 attached 的 remote parent 为父 → 同 trace_id,parent_span_id = CLIENT span id。

---

## 5. 组件设计

### 5.1 `instrumentors/gateway.py`(新建)

`instrument_gateway(tracer, *, message_handler_cls=None, agent_client_cls=None)`

**a) `channel.request` span** — wrap `MessageHandler.process_message` + `process_stream`:

```python
def factory_process(original):  # process_message (unary)
    async def traced(self, *args, **kw):
        attrs = _gateway_attrs(args, kw)  # channel_id/request_id 尽力提取
        with tracer.start_as_current_span("channel.request", kind=SpanKind.INTERNAL, attributes=attrs) as span:
            return await original(self, *args, **kw)
    return traced

def factory_process_stream(original):  # process_stream (async gen)
    async def traced(self, *args, **kw):
        attrs = _gateway_attrs(args, kw)
        with tracer.start_as_current_span("channel.request", kind=SpanKind.INTERNAL, attributes=attrs) as span:
            async for chunk in original(self, *args, **kw):
                yield chunk
    return traced
```

`_gateway_attrs`:从 args/kw 里尽力取 `channel_id`/`request_id`(可能在 message/envelope 对象上),设 `JIUWENCLAW_CHANNEL_ID`/`JIUWENCLAW_REQUEST_ID`;拿不到就留空。fail-soft。

**b) `jiuwenclaw.gateway.agent.request` CLIENT span + 注入** — wrap `WebSocketAgentServerClient.send_request` + `send_request_stream`:

```python
def factory_send(original):  # send_request (unary)
    async def traced(self, envelope):
        attrs = {JIUWENCLAW_CHANNEL_ID: _get(envelope, "channel_id", "")}
        with tracer.start_as_current_span("jiuwenclaw.gateway.agent.request", kind=SpanKind.CLIENT, attributes=attrs) as span:
            _inject_traceparent(envelope)  # CLIENT current 时注入
            return await original(self, envelope)
    return traced

def factory_send_stream(original):  # send_request_stream (async gen)
    async def traced(self, envelope):
        attrs = {JIUWENCLAW_CHANNEL_ID: _get(envelope, "channel_id", "")}
        with tracer.start_as_current_span("jiuwenclaw.gateway.agent.request", kind=SpanKind.CLIENT, attributes=attrs) as span:
            _inject_traceparent(envelope)  # 迭代前注入一次,所有 chunk 共享
            async for chunk in original(self, envelope):
                yield chunk
    return traced
```

`_inject_traceparent(envelope)`:
```python
def _inject_traceparent(envelope):
    carrier = envelope.channel_context if isinstance(envelope.channel_context, dict) else {}
    envelope.channel_context = carrier
    inject(carrier)  # opentelemetry.propagate.inject;CLIENT span current → traceparent 指向它
```

注:`send_request` 已会 mutate 调用方的 envelope(`envelope.is_stream=...`),mutate `channel_context` 一致。file-transfer 方法委托给 `send_request`,自动覆盖。

### 5.2 `instrumentors/agentserver.py`(新建)

`instrument_agentserver(tracer, *, adapter_cls=None)`

wrap `JiuWenClawDeepAdapter.process_message_impl` + `process_message_stream_impl`(注意:是 `_impl`,**不是**公开 `process_message`——公开方法与 `ReActAgent.invoke` 之间有 session manager 队列的 task 边界,ContextVar/attach 透不过去;`_impl` 与 `ReActAgent.invoke` 同 task)。

```python
def factory_impl(original):  # process_message_impl (unary)
    async def traced(self, request, inputs):
        token = _attach_remote_parent(request)
        try:
            return await original(self, request, inputs)
        finally:
            if token is not None:
                context.detach(token)
    return traced

def factory_impl_stream(original):  # process_message_stream_impl (async gen)
    async def traced(self, request, inputs):
        token = _attach_remote_parent(request)
        try:
            async for chunk in original(self, request, inputs):
                yield chunk
        finally:
            if token is not None:
                context.detach(token)
    return traced
```

`_attach_remote_parent(request)`:
```python
def _attach_remote_parent(request):
    try:
        carrier = (request.metadata if isinstance(request.metadata, dict) else {}) or {}
        extracted = extract(carrier)  # opentelemetry.propagate.extract
        remote = trace.get_current_span(extracted).get_span_context()
        if remote is not None and remote.is_valid:
            return context.attach(extracted)  # 现有 agent.invoke 的 start_as_current_span 自动以此续父
    except Exception:
        pass
    return None  # 无 traceparent → 不 attach,agent.invoke 仍为根 span(同今天)
```

### 5.3 `instrumentors/__init__.py` + `attributes.py`

`apply_instrumentors` 加两步(`cfg.traces_exporter != "none"` 时):

```python
("gateway", lambda: gateway.instrument_gateway(tracer)),
("agentserver", lambda: agentserver.instrument_agentserver(tracer)),
```

`attributes.py`:复用已有 `JIUWENCLAW_CHANNEL_ID` / `JIUWENCLAW_REQUEST_ID`(无需新增)。

### 5.4 不改 `agent.py` / `activate.py`

`agent.py` 的 `tracer.start_as_current_span("jiuwenclaw.agent.invoke", ...)` 会自动以 `context.attach` 设的 remote parent 为父。`activate.py` 启动日志不变(gateway/agentserver 是 trace 插桩,随 traces_exporter 一起)。

---

## 6. 数据流

```
gateway: web 消息 → MessageHandler.process_stream
  → [channel.request span current] → ... → send_request(envelope)
  → [jiuwenclaw.gateway.agent.request CLIENT span, channel.request 的子]
  → inject traceparent 进 envelope.channel_context (CLIENT current)
  → WS send (envelope.to_dict 序列化 traceparent) → [CLIENT span end]
        ↓ E2A wire (channel_context 带 traceparent,作普通 JSON key 透传,codec 不感知)
agentserver: agent_ws_server 收 → E2AEnvelope.from_dict → e2a_to_agent_request
  → channel_context → AgentRequest.metadata (agent_compat.py,traceparent 落 metadata)
  → JiuWenClawDeepAdapter.process_message_impl(request, inputs)
  → [extract traceparent from request.metadata → context.attach(remote)]
  → Runner.run_agent → ReActAgent.invoke
  → [agent.invoke span, remote CLIENT 的子(经 attach)] → gen_ai.chat / gen_ai.tool
  → [finally: context.detach]
```

---

## 7. 错误处理

- 全部 wrap 走 `patch_method`(幂等 + fail-soft)。
- 注入:`envelope.channel_context` 不是 dict(默认 `field(default_factory=dict)`,不会发生)→ 用 `{}`;`inject` 失败 → 不注入(WS 仍正常发)。
- 提取:`request.metadata` 是 None / 无 `traceparent` → `extract({})` 返空 context → `remote.is_valid == False` → **不 attach**(agent.invoke 仍根 span,同今天)。
- `context.attach/detach`:`token` 非 None 才 detach;detach 在 `finally`,异常/正常/提前 break 都解。
- file-transfer / 自定义 WS handler 路径(`metadata=None`)→ 无 traceparent → 退化独立 trace。

---

## 8. 测试

**`tests/instrumentors/test_gateway.py`(新建)** — fake `MessageHandler` + `WebSocketAgentServerClient` + `E2AEnvelope` + `CollectingSpanExporter`:
1. `test_send_request_injects_traceparent` — `send_request(env)` 后 `env.channel_context["traceparent"]` 非空 + 建 `jiuwenclaw.gateway.agent.request` CLIENT span。
2. `test_send_request_stream_injects_traceparent` — stream 变体,span 跨 `async for`,traceparent 注入一次。
3. `test_process_stream_creates_channel_request_span` — `process_stream` 建 `channel.request` span。
4. `test_channel_request_is_parent_of_client` — 两层 wrap 同跑,CLIENT 的 parent_span_id == channel.request 的 span_id(同 trace_id)。
5. `test_inject_failsoft_no_channel_context` — envelope 无 dict `channel_context` → 不崩,WS 仍发。

**`tests/instrumentors/test_agentserver.py`(新建)** — fake `JiuWenClawDeepAdapter` + `AgentRequest`:
1. `test_extract_attaches_remote_parent` — `request.metadata` 带某 span 注入的 traceparent → 调 `process_message_impl` → 其内 `start_as_current_span("child")` 的 trace_id == remote trace_id,parent_span_id == remote span_id。
2. `test_no_metadata_no_attach` — `request.metadata=None` → child span 是根(无 remote parent)。
3. `test_stream_variant` — `process_message_stream_impl` async gen,attach 在迭代前,detach 在 finally。
4. `test_end_to_end_trace_linking` — inject 进 carrier(模拟 gateway)→ extract+attach(模拟 agentserver)→ 起 span → 断言 parent trace_id == 注入 span 的 trace_id。

---

## 9. 验收

- `OTEL_TRACES_EXPORTER=otlp` 启动 agentserver + gateway,发一条「你好」。
- labubu 里该请求**一条 trace**:`channel.request` → `jiuwenclaw.gateway.agent.request` → `jiuwenclaw.agent.invoke` → `gen_ai.chat`/`gen_ai.tool`(端到端父子链)。
- 该 trace 详情下显示 gateway + agentserver 两边的关联日志(同 trace_id,**不被 5min 清**)。
- 关 `OTEL_TRACES_EXPORTER`(或 `OTEL_ENABLED=false`)→ 零成本 no-op;无 traceparent 时 agent.invoke 仍为根 span(不回归)。
- 单测全绿(新增 ~9 例)。
