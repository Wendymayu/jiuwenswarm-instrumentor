jiuwenswarm 智能体的可观测数据采集器 —— 独立、自包含的 OpenTelemetry 自动插桩，为 jiuwenclaw / openjiuwen 多通道 AI Agent 采集 traces + metrics，经 OTLP 导出至任意标准可观测后端（Arize Phoenix / Langfuse / 自托管 labubu，三者同讲 OTLP，仅端点不同）。

## 安装

```bash
pip install jiuwenswarm-instrumentor
```

> 注：本机默认 `python` 为 3.14，本包 `requires-python = ">=3.11,<3.14"`，请用 Python 3.11–3.13（如 `py -3.13`）。

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

> 注：本机默认 `python` 为 3.14，本包 `requires-python <3.14`，请用 Python 3.11–3.13（如 `py -3.13`）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `OTEL_ENABLED` | `false` | 总开关，关闭时零开销 no-op |
| `OTEL_TRACES_EXPORTER` | `none` | `otlp` / `console` / `none` |
| `OTEL_METRICS_EXPORTER` | `none` | `otlp` / `console` / `none` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` / `http` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | 后端地址（Phoenix/Langfuse/labubu） |
| `OTEL_SERVICE_NAME` | `jiuwenclaw` | 服务名 |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 逗号分隔 `k=v` 鉴权头 |
| `OTEL_LOG_MESSAGES` | `false` | 预留：完整消息内容采集（暂未实现） |
| `OTEL_MESSAGE_CONTENT_MAX_LENGTH` | `4096` | 预留：单条内容长度上限（暂未实现） |

## 采集的信号

- **Traces**：
  - `gen_ai.chat`（LLM 调用，含 token 用量、TTFT、上下文构成）
  - `gen_ai.tool`（工具执行）
  - `jiuwenclaw.agent.invoke`（Agent 调用，含 ReAct 迭代次数、reasoning tokens）
  - `jiuwenclaw.subagent.invoke`（子 Agent 调用，含正确 agent_name 与迭代数）
  - `jiuwenclaw.session.create` / `jiuwenclaw.session.end`（会话生命周期）
  - `gen_ai.context.compaction` / `gen_ai.context.memory_blocks`（上下文压缩与记忆块 token 桶）
- **Metrics**：`gen_ai.client.token.usage`、`gen_ai.client.operation.duration`、`gen_ai.tool.count` / `gen_ai.tool.duration`、`gen_ai.agent.duration`、上下文压缩次数 / `tokens_saved`。
- 语义约定遵循 OTel GenAI semconv（`gen_ai.*`）+ 自定义 `jiuwenclaw.*` 维度。

## 手动冒烟（smoke）

1. 启动后端（如 Phoenix：`python -m phoenix.server serve`）。
2. `OTEL_ENABLED=true OTEL_TRACES_EXPORTER=console jiuwen-instrument jiuwenclaw-app`。
3. 发一条对话，确认控制台 / 后端出现 `gen_ai.chat` / `gen_ai.tool` / `jiuwenclaw.agent.invoke` / `jiuwenclaw.session.*` span。

## 设计

无侵入、自包含的 OpenTelemetry 自动插桩：进程内 monkey-patch jiuwenclaw/openjiuwen 的核心运行时方法，自带 OTel 栈，OTLP 导出。不依赖 jiuwenclaw 内置（即将废弃的）`telemetry` 模块或其可观测扩展点。详见 `docs/superpowers/`。
