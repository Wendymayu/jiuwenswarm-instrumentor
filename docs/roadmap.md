# jiuwenswarm-instrumentor Roadmap

> 最后更新: 2026-06-26 · 分支: `feat/instrumentor-impl` · 93 tests
>
> 独立、自包含的 OpenTelemetry 自动插桩 Python 包,为 jiuwenclaw / openjiuwen 采集 traces + metrics + logs,经 OTLP 导出到 labubu / Phoenix / Langfuse。零 jiuwenclaw 源码改动(纯 monkey-patch);不依赖旧 `jiuwenclaw/telemetry/`。

---

## 已实现特性

### 1. Traces( spans)

| span | 挂载点 | 关键属性 |
|---|---|---|
| `gen_ai.chat` | `OpenAIModelClient.invoke` / `.stream` | `gen_ai.request.model`、`usage.input/output/total_tokens`、`streaming.first_token_ms`、`response.finish_reason`、`gen_ai.input/output.messages`、`gen_ai.tool.definitions`、`gen_ai.context.*`(7 桶)、`gen_ai.usage.estimated`、`gen_ai.operation.name`(`chat` / `session_memory_update` / `full_compact_summary`) |
| `gen_ai.tool` | `AbilityManager.execute_single` | `gen_ai.tool.name`、`call.id`、`arguments`/`result`、skill 丰富(`gen_ai.operation.name=load_skill/release_skill`、`gen_ai.skill.name`、`gen_ai.skill.id`、`skill.loaded`/`skill.released` 事件) |
| `jiuwenclaw.agent.invoke` | `ReActAgent.invoke` | `gen_ai.agent.name`、`jiuwenclaw.session.id` |
| `jiuwenclaw.session.create` / `.end` | `JiuWenClaw.create_instance` / `.cleanup` | `jiuwenclaw.session.id` |
| `channel.request` | `MessageHandler.process_message` / `.process_stream`(gateway) | `jiuwenclaw.channel.id`、`jiuwenclaw.request.id`(尽力) |
| `jiuwenclaw.gateway.agent.request` | `WebSocketAgentServerClient.send_request` / `.send_request_stream`(gateway) | CLIENT span,注入 W3C traceparent 进 `envelope.channel_context` |
| `context.compaction` | `SessionModelContext.add_messages`(ADD 整体)+ 3 GET processor 的 `on_get_context_window`(per-processor) | `context.compaction.path`(ADD/GET)、`processor_type`、`tokens_before/after/saved`、`messages_before/after`、`jiuwenclaw.session.id`、`jiuwenclaw.context.id` |

### 2. Metrics

| metric | 类型 | 说明 |
|---|---|---|
| `gen_ai.client.token.usage` | Counter | input/output token,by `gen_ai.token.type` |
| `gen_ai.client.operation.duration` | Histogram | LLM 调用耗时 |
| `gen_ai.tool.count` / `gen_ai.tool.duration` | Counter / Histogram | 工具执行次数/耗时 |
| `gen_ai.agent.duration` | Histogram | agent.invoke 耗时 |
| `gen_ai.skill.call.count` / `.duration` / `.error.count` | Counter / Histogram / Counter | skill 调用/耗时/错误 |
| `gen_ai.skill.token.usage` / `gen_ai.tool.token.usage` | Counter | 上下文里 per-skill / per-tool 的 token 占用 |
| `gen_ai.context.compaction.count` / `.tokens_saved` | Counter / Histogram | 压缩次数/省的 token(按 path+processor_type) |

### 3. Logs

- jiuwenclaw stdlib `logging` → OTel `LogRecord` → OTLP `/v1/logs` → labubu
- **trace 关联**:每条 LogRecord 带 `context=get_current()` 的 trace_id/span_id → labubu `GET /api/v1/logs/:traceId` 关联
- **filter 复用**:从 `jiuwenclaw` logger 已有 handler 复制 `SensitiveDataFilter` 等 → 脱敏一致;无 filter 时回退 WARNING-only
- **import-time `setup_logger` 修复**:`instrument_logs` 末尾加 `attach()` 重挂 handler(jiuwenclaw `utils.py:2821` 的模块级 `setup_logger()` 会在 patch 安装前清空 handler)
- **路由 filter 不丢日志**:`OTelLogHandler.handle` 重写,filter 只为副作用(脱敏)跑、不拒绝记录

### 4. 上下文 token 归因

7 个 `gen_ai.context.*` span 属性(tiktoken 估算,`gen_ai.usage.estimated=true` 标记):

| 属性 | 来自 |
|---|---|
| `gen_ai.context.skill` | skill body(tool msg `is_skill_body`)+ skill pin(system msg `active_skill_pin`) |
| `gen_ai.context.system_prompt` | system msg(非 skill pin) |
| `gen_ai.context.user_messages` | user msg(非记忆块) |
| `gen_ai.context.assistant_messages` | assistant msg(含 `tool_calls` JSON + `reasoning_content`) |
| `gen_ai.context.tool_results` | tool msg(非 skill body) |
| `gen_ai.context.tool_definitions` | 工具定义(按 `<\|start\|>functions.{name}:{idx}\n{json}<\|end\|>` framing 计数) |
| `gen_ai.context.memory_blocks` | 记忆块(content 前缀 `[DIALOGUE_MEMORY_BLOCK]`/`[FULL_COMPACT_BOUNDARY]`/`<memory-context>` 等,reclassify 不重复计 role 桶) |

- 分词器:`tiktoken.encoding_for_model(model_name)` → `cl100k_base` → `len//4`(按模型解析,encoding 缓存)
- 方程:`skill + system_prompt + user_messages + assistant_messages + tool_results + tool_definitions + memory_blocks ≈ gen_ai.usage.input_tokens`

### 5. 端到端 trace 传播

W3C TraceContext(`traceparent`)跨 gateway↔agentserver WS 边界:

```
channel.request (gateway, root)
└─ jiuwenclaw.gateway.agent.request (gateway, CLIENT, 注入 traceparent)
   └─ jiuwenclaw.agent.invoke (agentserver, 经 context.attach 续父)
      ├─ gen_ai.chat
      └─ gen_ai.tool
```

- gateway 注入:`inject(envelope.channel_context)`(CLIENT span current 时)
- agentserver 提取:`extract(request.metadata)` → `context.attach`(在 `process_message_impl[_stream]`,与 `ReActAgent.invoke` 同 task)
- 两边 jiuwenclaw 日志都带同一 `trace_id` → labubu 关联保留(不被 5min 清)

### 6. 上下文压缩事件

- **ADD 路径(整体)**:wrap `SessionModelContext.add_messages` → buffer 前后 token delta(NET,非纯压缩 —— 会 net 掉 `add_back` 的新消息)
- **GET 路径(per-processor)**:wrap `FullCompactProcessor` / `RoundLevelCompressor` / `ToolResultDedupProcessor` 的 `on_get_context_window` → window 前后 delta(GROSS,纯压缩)
- 只在 `after < before` 时出 span + metric;`IrreducibleContextError` 透传
- 用引擎自己的 `context.token_counter()` 计数(账本一致)

### 7. Skill 可观测

- **Layer 1(span 属性 + 事件)**:在 `skill_tool` / `skill_complete` 的 `gen_ai.tool` span 上设 `gen_ai.operation.name=load_skill/release_skill`、`gen_ai.skill.name`、`gen_ai.skill.id`、`skill.loaded`/`skill.released` 事件
- **Layer 2(3 个指标)**:`gen_ai.skill.call.count` / `.duration` / `.error.count`
- **会话范围 duration 状态**:模块级 dict(`(session_id, skill_name) -> start_monotonic`),跨 asyncio task

### 8. 记忆可观测

- **记忆块 token 分桶**:`gen_ai.context.memory_blocks` —— 8 个 content 前缀 marker 识别,reclassify 不重复计
- **记忆更新 LLM 标记**:`gen_ai.operation.name = session_memory_update | full_compact_summary` —— 嗅探 system prompt 前缀

### 9. 基础设施

| 模块 | 职责 |
|---|---|
| `config.py` | `InstrumentorConfig` + `load_config()`(`OTEL_*` env 驱动,per-signal exporter/endpoint/protocol/headers) |
| `provider.py` | `init_providers()`(TracerProvider + MeterProvider + LoggerProvider + OTLP gRPC/HTTP + console,fail-soft) |
| `wrap.py` | `patch_method(cls, name, factory)`(幂等 + fail-soft monkey-patch) |
| `context.py` | `set_request_context()` / `current_request_attrs()`(ContextVar 传播,`_RequestContextToken` 包装) |
| `activate.py` | `activate()` + `main()` CLI(`jiuwen-instrument <module> [args...]`,argv 剥离模块名) |
| `attributes.py` | 所有 `gen_ai.*` + `jiuwenclaw.*` + `context.compaction.*` 常量 |
| `metrics.py` | `Metrics` 类(所有 Counter/Histogram + fail-soft record 方法) |

### 10. 文档

| 文档 | 内容 |
|---|---|
| `docs/guides/start-jiuwenswarm-with-instrumentor.md` | 启动指南(env、setup、启动命令、验证、常见坑) |
| `docs/troubleshooting/streaming-tool-span-parentage.md` | 流式工具 span 父子关系修复 |
| `docs/troubleshooting/logs-dropped-by-app-routing-filters.md` | 路由 filter 丢日志问题修复 |
| `docs/troubleshooting/gateway-process-stream-coroutine-vs-async-generator.md` | process_stream coroutine vs async generator 踩坑 |
| `docs/superpowers/specs/` | 各特性的设计规格(6 份) |
| `docs/superpowers/plans/` | 各特性的实现计划(6 份) |

---

## 测试

- **93 个单测**,全部通过(`py -3.13 -m pytest`)
- 覆盖:config、provider、wrap、context、attributes、metrics、llm、tool、agent、session、skill、logs、gateway、agentserver、context_tokens、context_compaction、apply

---

## 未做 / 未来方向

> 注:以下仅列**需要新代码、无法从已采集数据派生**的项。prompt cache 命中率、成本估算、session 活跃数、端到端延迟、错误分类、对话流、工具选择模式、流式吞吐、并发、响应质量、per-agent 成本、LLM 成功率、模型分布、流式完成率、工具结果大小等均可由后端从已有 span 属性/metric 直接计算,不在列表内。

| 方向 | 价值 | 说明 |
|---|---|---|
| per-channel 指标 label | 中 | gateway wrap 加 `set_request_context(channel_id=...)` → 下游 metric label 自动带 channel_id(2 行改动) |
| ADD-path gross tokens_saved | 中 | 当前是 NET(压缩 - 新增),需单独数 `add_back` 的 token → gross |
| Session memory CRUD 事件 | 中 | wrap `SessionMemoryManager` 的 update/commit |
| External LTM CRUD/recall | 低(默认关) | wrap `LongTermMemory`(LTM `engine: none` 默认关) |
| identity 标签 | 低 | `user.id`/`domain.id`/`app.id`(需 IdentityStore,自包含约束) |
