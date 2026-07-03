jiuwenswarm 智能体的可观测数据采集器 —— 独立、自包含的 OpenTelemetry 自动插桩，为 jiuwenclaw / openjiuwen 多通道 AI Agent 采集 traces + metrics，经 OTLP 导出至任意标准可观测后端（Arize Phoenix / Langfuse / 自托管 labubu，三者同讲 OTLP，仅端点不同）。

## 使用（无侵入）

默认 exporter=otlp / protocol=grpc / endpoint=localhost:4317 / 采集完整 prompt/response —— 本地 gRPC 后端（labubu / Phoenix 默认 4317）**只设一个变量**：

```bash
pip install jiuwenswarm-instrumentor          # 1. 装（非 editable，自带自动加载钩子）
export OTEL_INSTRUMENTOR_ENABLED=true         # 2. 唯一必设项（后端在本地 4317 gRPC 时）
python -m jiuwenclaw.app                      # 3. 跑——父 + agentserver + gateway 子进程全自动插桩
```

> ⚠️ **enterprise_dev 双开关**：jiuwenswarm 内置可观测模块读 `OTEL_ENABLED`，本独立探针读 `OTEL_INSTRUMENTOR_ENABLED`——两者分离，避免同时安装时数据重复上报。要内置就只设 `OTEL_ENABLED=true`；要用本探针就只设 `OTEL_INSTRUMENTOR_ENABLED=true`（并确认内置未开）。**等内置模块移除后，本探针会改回读 `OTEL_ENABLED`，恢复单开关。**

后端非本地或用 HTTP（如 Langfuse 云端）再加：
```bash
export OTEL_EXPORTER_OTLP_PROTOCOL=http
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-backend
```

> **Windows 用户**：`export` 是 bash 语法，cmd/PowerShell 不认。cmd 用 `set OTEL_INSTRUMENTOR_ENABLED=true`，PowerShell 用 `$env:OTEL_INSTRUMENTOR_ENABLED="true"`，**设完在同一窗口立刻启动**。完整三平台写法见 `docs/observability-quickstart.md`。自检：`set OTEL_INSTRUMENTOR_ENABLED`(cmd)/`echo $env:OTEL_INSTRUMENTOR_ENABLED`(PS) 应非空。

**成功的判据是后端 UI 能查到 `service=jiuwenclaw` 的 trace**（`[instrumentor] active` 这行 INFO 在 `.pth` 自动加载时可能不显示，不影响上报）。无需改 jiuwenclaw 源码、无需 CLI 包裹：装包时随附的 `jiuwenswarm_instrumentor.pth` 让每个 Python 进程（含 `app.py` fork 的两个子进程）启动即自动激活。

> 🔒 默认 `OTEL_LOG_MESSAGES=true` 采集完整 prompt/response + tool 参数/结果。隐私敏感场景设 `false`。

> 用 Python 3.11–3.13（如 `py -3.13`），本包 `requires-python <3.14`。editable 安装不发货 `.pth`，要自动加载须非 editable。
>
> 单进程入口想用 CLI 包裹：`jiuwen-instrument your_app`。⚠️ 不要用 `jiuwen-instrument jiuwenclaw.app`——它只覆盖父进程，子进程不被插桩。
>
> 最简上手见 `docs/observability-quickstart.md`。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `OTEL_INSTRUMENTOR_ENABLED` | `false` | **本探针总开关**，关闭时零开销 no-op。与 `OTEL_ENABLED` 分离（后者控制 jiuwenclaw 内置可观测模块），避免双上报 |
| `OTEL_ENABLED` | `false` | jiuwenclaw **内置**可观测模块的总开关（本探针不读它）。内置模块移除后本探针将改回读它 |
| `JIUWENSWARM_INSTRUMENT_AUTOLOAD` | (未设) | 设 `false`/`0`/`no`/`off` 关闭 `.pth` 自动加载，即使 `OTEL_INSTRUMENTOR_ENABLED=true` 也不自动激活（改用 CLI/代码激活时用） |
| `OTEL_TRACES_EXPORTER` | `otlp` | `otlp` / `console` / `none` |
| `OTEL_METRICS_EXPORTER` | `otlp` | `otlp` / `console` / `none` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` / `http`（HTTP 后端如 Langfuse 改 `http`） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | 后端地址（Phoenix/Langfuse/labubu） |
| `OTEL_SERVICE_NAME` | `jiuwenclaw` | 服务名 |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 逗号分隔 `k=v` 鉴权头 |
| `OTEL_LOG_MESSAGES` | `true` | 采集完整 prompt/response(`gen_ai.input.messages`/`gen_ai.output.messages`)+ tool 参数/结果。**隐私敏感设 `false`** |
| `OTEL_MESSAGE_CONTENT_MAX_LENGTH` | `4096` | 单条消息/工具内容字符截断阈值。设 `0` / `none` / `off` 关闭截断(采集完整内容,体积更大)。日志体见 `OTEL_LOG_MESSAGE_MAX_LENGTH`(默认 `8192`,同样支持 `0`) |
| `OTEL_LOGS_EXPORTER` | `otlp` | jiuwenclaw stdlib 日志导出:`otlp` / `console` / `none`。默认开,trace 页可看执行期日志 |
| `OTEL_LOGS_LEVEL` | `INFO` | 日志采集级别(`DEBUG` 爆量) |
| `OTEL_INSTRUMENT_GATEWAY` | `false` | 是否采集 gateway 可观测数据(`channel.request`/`jiuwenclaw.gateway.agent.request` span + traceparent 注入)。默认关:与 agent 行为无关,`agent.invoke` 直接作为根 trace。想看 gateway 或要跨进程链路时设 `true` |

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
OTEL_INSTRUMENTOR_ENABLED=true OTEL_TRACES_EXPORTER=console python -m jiuwenclaw.app
```

发一条对话,看到 `gen_ai.chat` / `gen_ai.tool` / `jiuwenclaw.agent.invoke` / `jiuwenclaw.session.*` span 即通。

## 设计

无侵入、自包含的 OpenTelemetry 自动插桩：进程内 monkey-patch jiuwenclaw/openjiuwen 的核心运行时方法，自带 OTel 栈，OTLP 导出。不依赖 jiuwenclaw 内置（即将废弃的）`telemetry` 模块或其可观测扩展点。详见 `docs/superpowers/`。
