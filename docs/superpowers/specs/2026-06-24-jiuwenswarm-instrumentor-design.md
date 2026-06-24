# jiuwenswarm-instrumentor 设计规格（Design Spec）

- **日期**: 2026-06-24
- **状态**: Draft — 待用户评审
- **作者**: brainstorming session

---

## 1. 背景与目标

`jiuwenclaw`（JiuWenClaw，基于 Python 的多通道 AI Agent）当前内置了一套
`jiuwenclaw/telemetry/` 可观测模块。**该模块后续会被废弃删除**（连同其 OTLP 导出实现与
`TelemetryProviderExtension` / `ExtensionRegistry` 可观测扩展点）。

本项目 `jiuwenswarm-instrumentor` 的目标:**以独立、自包含、无侵入的方式**,重新实现一套
针对 jiuwenclaw / openjiuwen 运行时的 OpenTelemetry 自动插桩,采集 **traces + metrics**,
通过标准 **OTLP** 导出到可观测后端(Arize Phoenix / Langfuse / 自建 labubu —— 三者协议一致,
仅 endpoint 不同)。

### 关键约束(来自需求方)

1. **不依赖任何现有可观测实现** —— 新包**禁止** `import jiuwenclaw.telemetry.*` 的任何内容,
   禁止使用 `TelemetryProviderExtension` / `ExtensionRegistry` 的 telemetry hook,禁止复用
   TelemetryRail、provider、exporter、config、attributes 现有代码。这些被删后新包必须照常工作。
2. **无侵入** —— 不改动 jiuwenclaw 业务源码(通过进程内自动 monkey-patch 实现)。
3. **标准后端** —— OTLP 导出,gen_ai.* 语义约定,后端可替换。

> 说明:`jiuwenclaw/telemetry/` 仅作为**知识参考**(了解哪些 gen_ai 属性、哪些 span 类别有价值),
> 不作为代码依赖。具体要 patch 的核心 API 一律从 openjiuwen / jiuwenclaw 的**核心代码**确认,
> 而非 telemetry 模块。

---

## 2. 非目标（Non-goals）

- **不做 logs** —— 本期范围仅 traces + metrics（需求方明确）。
- **不做后端** —— 不自建 Collector / 存储；数据导出到现成的 Phoenix / Langfuse / labubu。
- **不依赖 jiuwenclaw 的 config.yaml** —— 配置全部走标准 `OTEL_*` 环境变量。
- **不覆盖 officeclaw（旧 ReActAgent）架构** —— 聚焦 enterprise_dev（SDK Adapter / deep_agent）。
  旧架构的支持可作为后续可选项。

---

## 3. 设计原则

| 原则 | 含义 |
|------|------|
| **自包含** | 自带 OTel SDK + OTLP exporter，自带 config / attributes，零现有可观测代码依赖 |
| **无侵入** | 进程内 monkey-patch 核心运行时方法，jiuwenclaw 源码零改动 |
| **fail-soft** | 任何 patch / 初始化失败只告警并降级，**绝不阻断** jiuwenclaw 主进程 |
| **可关停** | `OTEL_ENABLED=false`（默认）时全程 no-op、零开销 |
| **隐私优先** | 默认只采集元数据 + token 用量；完整 prompt/response 内容走显式 opt-in |

---

## 4. 架构

```
                         jiuwenswarm-instrumentor（独立 pip 包）
                         ┌──────────────────────────────────────────────┐
   jiuwenclaw /          │  activate.py   ← 启动时注入（CLI 包装 / sitecustomize / setup()）│
   openjiuwen 核心方法    │        │                                       │
        │                │        ▼                                       │
        │  import 时      │  instrumentors/  ──wrap──►  OTel SDK           │
        │  monkey-patch   │   llm / tool / agent / session   gen_ai.* attrs │
        ▼                │        │              + jiuwenclaw.* custom     │
   （被 wrap 的核心调用） │        ▼                                       │
                        │  provider.py → OTLP exporter (gRPC/HTTP)        │
                        └──────────────────────────┬───────────────────────┘
                                                   │ OTLP (traces + metrics)
                                                   ▼
                                  Phoenix  /  Langfuse  /  labubu
```

数据流：核心方法被 wrap → 生成 gen_ai span / metric → OTel SDK → OTLP exporter → 后端。

---

## 5. 组件（Components）

### 5.1 `config.py` —— 配置

- 完全走标准 `OTEL_*` 环境变量（不读 jiuwenclaw 的 yaml）：
  - `OTEL_ENABLED`（默认 `false`，关停即 no-op）
  - `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_PROTOCOL`（`grpc` / `http`）
  - `OTEL_TRACES_EXPORTER`、`OTEL_METRICS_EXPORTER`（`otlp` / `console` / `none`）
  - `OTEL_EXPORTER_OTLP_HEADERS`
  - `OTEL_SERVICE_NAME`（默认 `jiuwenclaw`）
  - `OTEL_LOG_MESSAGES`（默认 `false`；是否记录完整消息内容）
- 数据类 `InstrumentorConfig`，frozen、env 驱动、带合理默认值。

### 5.2 `provider.py` —— 自包含 OTel 栈

- 自建 `TracerProvider` + `MeterProvider` + `Resource`（service.name / service.version）。
- OTLP exporter：gRPC（`otlp.proto.grpc`）与 HTTP（`otlp.proto.http`，`/v1/traces`、`/v1/metrics`）按 protocol 选择。
- traces 用 `BatchSpanProcessor`；metrics 用 `PeriodicExportingMetricReader`。
- `init_providers(cfg)`：失败 try/except 包裹，失败告警并跳过（fail-soft）。

### 5.3 `attributes.py` —— 语义属性常量

- OTel GenAI 标准常量（`gen_ai.system`、`gen_ai.request.model`、`gen_ai.usage.input_tokens` …），
  直接抄 OTel GenAI semconv 定义（这些是 OTel 规范，不属于 jiuwenclaw）。
- 自定义维度：`jiuwenclaw.session.id`、`jiuwenclaw.channel.id`、`jiuwenclaw.request.id`、
  `jiuwenclaw.claw.id` 等（仅是字符串常量定义，无运行时依赖）。

### 5.4 `instrumentors/` —— 插桩（核心）

| 插桩 | span / metric | 目标（核心 API，类别 + 锚点） |
|------|---------------|------------------------------|
| **LLM** | `gen_ai.chat` span + token 用量 + TTFT | openjiuwen 模型 client 的 chat / stream 方法（锚点 `openjiuwen.core.foundation.llm.model_clients.openai_model_client.OpenAIModelClient`，含 `_stream_with_retry`） |
| **Tool** | `gen_ai.tool` span（args / result / duration） | openjiuwen 工具/ability 执行器（ability_manager 的 execute 路径） |
| **Agent** | agent invoke span + 会话上下文传播 | openjiuwen runner / jiuwenclaw deep_agent 的 invoke/run |
| **Session** | 会话生命周期 span + stuck 检测 metric | jiuwenclaw SessionManager（create/end） |

- 公共 `instrumentors/_wrap.py`：`safe_wrap(obj, method, wrapper)` —— 统一 fail-soft monkey-patch
  助手（保留原方法、try/except、可回滚）。
- **精确方法签名**：在实现计划阶段，对照所 pin 的 openjiuwen 版本，从其源码逐一确认
  （读 openjiuwen 的 git ref / venv site-packages）。本规格只锁类别与锚点。

### 5.5 `metrics.py` —— 指标仪表

- LLM：`gen_ai.client.token.usage`（counter，区分 input/output）、`gen_ai.client.operation.duration`（histogram）、调用计数。
- Tool：tool 调用计数、tool duration、tool error 计数。
- Agent：invoke 计数与 duration。
- Session：活跃会话 gauge、stuck 计数。
- 优先用 OTel GenAI semconv 定义的 metric 名；无标准定义处用 `jiuwenclaw.*` 自定义名。

### 5.6 `activate.py` —— 激活方式（无侵入）

三种激活路径，按推荐度排序：

1. **CLI 包装（主推）** —— console-script 入口 `jiuwen-instrument`，
   类似 `opentelemetry-instrument`：`jiuwen-instrument jiuuwenclaw-start …`
   在子进程启动、jiuwenclaw import 之前注入插桩。零源码改动、最可靠。
2. **sitecustomize（零触碰便捷）** —— 安装即生效，但用 `OTEL_ENABLED=1` 环境变量门控，
   避免影响无关 Python 进程。
3. **显式 `setup()`（兜底）** —— `from jiuwenswarm_instrumentor import setup; setup()`，
   最可控，代价是需在 jiuwenclaw 启动处加一行。

> 三者共用同一套 `init_providers()` + `apply_instrumentors()`，只是触发时机不同。

---

## 6. 激活与数据流（运行时顺序）

1. Python 启动 → `activate.py`（CLI 包装或 sitecustomize 或 setup()）触发。
2. `init_providers(cfg)`：建 TracerProvider / MeterProvider / OTLP exporter。
3. `apply_instrumentors()`：在 jiuwenclaw / openjiuwen 被 import 时（或 import 后立即）wrap 核心方法。
4. jiuwenclaw 运行 → 被 wrap 的方法产出 gen_ai span / metric → BatchSpanProcessor 批量导出。
5. OTLP → 后端。

---

## 7. 错误处理 / fail-soft

- 所有 monkey-patch 包在 `safe_wrap` 的 try/except 中：失败 → 记日志告警 → 保留原方法 → 不抛。
- provider 初始化失败：告警并跳过，jiuwenclaw 照常运行。
- `OTEL_ENABLED=false`（默认）：`apply_instrumentors` 直接返回，零开销。
- token 用量提取依赖 provider 响应结构：取不到时用 tiktoken 估算并标 `gen_ai.usage.estimated=true`（估算失败也不抛）。

---

## 8. 隐私 / 内容处理

- 默认**不**记录完整 prompt/response 内容，只记元数据 + token 用量。
- 完整消息内容走 `OTEL_LOG_MESSAGES=true` 显式开启；并对单条长度封顶
  （`OTEL_MESSAGE_CONTENT_MAX_LENGTH`，默认 4096）。
- 理由：traces 可能进入共享后端（Langfuse / labubu），默认保守。

---

## 9. 测试策略

- **单元测试**：每个 instrumentor 用 mock / fake 核心对象（不发起真实 LLM 调用），
  断言产出的 span 属性、metric 值正确。
- **集成测试**：起一个本地测试 OTLP 接收端（内存 / 测试 HTTP receiver），
  断言导出的 payload 含 gen_ai.* 属性、token 用量、metric 数据点。
- **fail-soft 测试**：patch 目标方法不存在 / 签名变更时，确认不崩溃、只告警。
- **版本契约测试**：pin 一个 openjiuwen 版本，记录被 patch 的方法清单，便于升级时回归。

---

## 10. 项目布局

```
jiuwenswarm-instrumentor/
├─ pyproject.toml
├─ README.md
├─ src/jiuwenswarm_instrumentor/
│  ├─ __init__.py            # setup() 公共入口
│  ├─ config.py              # OTEL_* env 配置
│  ├─ provider.py            # 自包含 OTel TracerProvider + MeterProvider + OTLP
│  ├─ attributes.py          # gen_ai.* + jiuwenclaw.* 常量
│  ├─ metrics.py             # 指标仪表
│  ├─ context.py             # 请求/会话上下文传播
│  ├─ activate.py            # CLI 包装 / sitecustomize / setup 入口
│  └─ instrumentors/
│     ├─ __init__.py         # apply_instrumentors()
│     ├─ _wrap.py            # safe_wrap fail-soft 助手
│     ├─ llm.py
│     ├─ tool.py
│     ├─ agent.py
│     └─ session.py
└─ tests/
```

---

## 11. 依赖

- Python `>=3.11,<3.14`（对齐 jiuwenclaw）。
- 运行时核心依赖（自带，独立于 jiuwenclaw）：
  - `opentelemetry-api`
  - `opentelemetry-sdk`
  - `opentelemetry-exporter-otlp-proto-grpc`
  - `opentelemetry-exporter-otlp-proto-http`
- 可选：`tiktoken`（provider 不返回用量时估算 token 数）。
- **被插桩对象**：`openjiuwen`（agent-core）、`jiuwenclaw` —— 作为运行环境存在，
  pin 版本以稳定 patch 目标。

---

## 12. 风险与权衡

| 风险 | 说明 | 缓解 |
|------|------|------|
| 核心 API 漂移 | monkey-patch 耦合 openjiuwen/jiuwenclaw 内部方法稳定性 | pin 版本 + 配置开关 + fail-soft + 方法清单回归 |
| TTFT / 流式 patch 组合 | jiuwenclaw 自身可能也 patch 了 `_stream_with_retry`，wrap 顺序冲突 | wrap 时检测并组合既有 patch（chain 原方法） |
| token 用量结构差异 | 不同 provider 响应结构不同 | 优雅降级 + tiktoken 估算 + estimated 标记 |
| 后端差异 | labubu 细节未知 | 标准的 OTLP（gRPC/HTTP）+ 可配 endpoint，假设等同 Langfuse |

---

## 13. 待确认 / 后续

- **精确 patch 目标**：实现计划阶段对照 openjiuwen pinned 版本逐一确认（本规格锁类别）。
- **labubu endpoint/port**：作为配置项，默认 `http://localhost:4317`。
- **officeclaw 旧架构**：本期不做，留作可选。
- **CLI 包装 vs sitecustomize 最终取舍**：实现时二选一为主推（倾向 CLI 包装）。
