jiuwenswarm 智能体的可观测数据采集器 —— 独立、自包含的 OpenTelemetry 自动插桩，为 jiuwenclaw / openjiuwen 多通道 AI Agent 采集 traces + metrics，经 OTLP 导出至任意标准可观测后端（Arize Phoenix / Langfuse / 自托管 labubu，三者同讲 OTLP，仅端点不同）。

## 使用（无侵入，三步）

```bash
pip install jiuwenswarm-instrumentor          # 1. 装（非 editable，自带自动加载钩子）
export OTEL_ENABLED=true                      # 2. 开总开关
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # 你的后端（Phoenix/Langfuse/labubu）
python -m jiuwenclaw.app                      # 3. 跑——父 + agentserver + gateway 子进程全自动插桩
```

日志出现 `[instrumentor] active` 即成功。无需改 jiuwenclaw 源码、无需 CLI 包裹：装包时随附的 `jiuwenswarm_instrumentor.pth` 让每个 Python 进程（含 `app.py` fork 的两个子进程）启动即自动激活。

> 用 Python 3.11–3.13（如 `py -3.13`），本包 `requires-python <3.14`。editable 安装不发货 `.pth`，要自动加载须非 editable。
>
> 单进程入口想用 CLI 包裹：`jiuwen-instrument your_app`。⚠️ 不要用 `jiuwen-instrument jiuwenclaw.app`——它只覆盖父进程，子进程不被插桩。
>
> 最简上手见 `docs/observability-quickstart.md`。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `OTEL_ENABLED` | `false` | 总开关，关闭时零开销 no-op |
| `JIUWENSWARM_INSTRUMENT_AUTOLOAD` | (未设) | 设 `false`/`0`/`no`/`off` 关闭 `.pth` 自动加载，即使 `OTEL_ENABLED=true` 也不自动激活（改用 CLI/代码激活时用） |
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

## 冒烟

不想接后端,把 exporter 设成 `console`,span 直接打到终端:

```bash
OTEL_ENABLED=true OTEL_TRACES_EXPORTER=console python -m jiuwenclaw.app
```

发一条对话,看到 `gen_ai.chat` / `gen_ai.tool` / `jiuwenclaw.agent.invoke` / `jiuwenclaw.session.*` span 即通。

## 设计

无侵入、自包含的 OpenTelemetry 自动插桩：进程内 monkey-patch jiuwenclaw/openjiuwen 的核心运行时方法，自带 OTel 栈，OTLP 导出。不依赖 jiuwenclaw 内置（即将废弃的）`telemetry` 模块或其可观测扩展点。详见 `docs/superpowers/`。
