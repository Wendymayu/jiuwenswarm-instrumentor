# jiuwenswarm 智能体的可观测数据采集器

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
export OTEL_SERVICE_NAME=jiuwenswarm
jiuwen-instrument jiuwenswarm.server.app_agentserver
jiuwen-instrument jiuwenswarm.gateway.app_gateway
```

或在程序入口显式激活（兜底）：

```python
import jiuwenswarm_instrumentor; jiuwenswarm_instrumentor.setup()
```

> 注：本机默认 `python` 为 3.14，本包 `requires-python <3.14`，请用 Python 3.11–3.13（如 `py -3.13`）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `OTEL_ENABLED` | `false` | 总开关，关闭时零开销 no-op |
| `OTEL_TRACES_EXPORTER` | `none` | `otlp` / `console` / `none` |
| `OTEL_METRICS_EXPORTER` | `none` | `otlp` / `console` / `none` |
| `OTEL_LOGS_EXPORTER` | `none` | `otlp` / `console` / `none` |
| `OTEL_LOGS_LEVEL` | `INFO` | 日志采集级别 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` / `http` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | 后端地址（Phoenix/Langfuse/labubu） |
| `OTEL_SERVICE_NAME` | `jiuwenswarm` | 服务名 |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 逗号分隔 `k=v` 鉴权头 |
| `OTEL_LOG_MESSAGES` | `true` | 完整消息内容采集（prompt/response + tool 参数/结果 + agent 用户输入）。隐私敏感设 `false` |
| `OTEL_MESSAGE_CONTENT_MAX_LENGTH` | `4096` | 单条消息/工具内容字符截断阈值。设 `0` / `none` / `off` 关闭截断（采集完整内容，体积更大）。日志体见 `OTEL_LOG_MESSAGE_MAX_LENGTH`（默认 `8192`，同样支持 `0`） |
| `OTEL_INSTRUMENT_GATEWAY` | `false` | 是否采集 gateway 可观测数据(`channel.request`/`jiuwenswarm.gateway.agent.request` span + traceparent 注入)。默认关:与 agent 行为无关,`agent.invoke` 直接作为根 trace。想看 gateway 或要跨进程链路时设 `true` |

## 采集的信号

- **Traces**：`gen_ai.chat`（LLM 调用，含 token 用量、TTFT、上下文 token 归因）、`gen_ai.tool`（工具执行，含 skill 丰富）、`jiuwenclaw.agent.invoke`（Agent 调用）、`jiuwenclaw.session.create` / `.end`（会话生命周期）、`channel.request` + `jiuwenclaw.gateway.agent.request`（gateway 端到端 trace 串联）、`context.compaction`（上下文压缩事件）。
- **Metrics**：`gen_ai.client.token.usage`、`gen_ai.client.operation.duration`、`gen_ai.tool.count` / `gen_ai.tool.duration`、`gen_ai.agent.duration`、`gen_ai.skill.call.count` / `.duration` / `.error.count`、`gen_ai.skill.token.usage` / `gen_ai.tool.token.usage`、`gen_ai.context.compaction.count` / `.tokens_saved`。
- **Logs**：jiuwenswarm stdlib 日志 → OTel logs（含 trace 关联）。
- 语义约定遵循 OTel GenAI semconv（`gen_ai.*`）+ 自定义 `jiuwenswarm.*` 维度。

## 手动冒烟（smoke）

1. 启动后端（如 labubu：`go run -tags dev ./cmd/labubu serve`）。
2. `OTEL_ENABLED=true OTEL_TRACES_EXPORTER=console OTEL_LOGS_EXPORTER=console jiuwen-instrument jiuwenswarm.server.app_agentserver`。
3. 发一条对话，确认控制台 / 后端出现 `gen_ai.chat` / `gen_ai.tool` / `jiuwenclaw.agent.invoke` / `jiuwenclaw.session.*` span + 日志。

## 设计

无侵入、自包含的 OpenTelemetry 自动插桩：进程内 monkey-patch jiuwenswarm/openjiuwen 的核心运行时方法，自带 OTel 栈，OTLP 导出。不依赖 jiuwenswarm 内置的可观测模块。详见 `docs/roadmap.md` + `docs/superpowers/`。
