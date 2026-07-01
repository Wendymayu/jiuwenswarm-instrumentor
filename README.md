jiuwenswarm 智能体的可观测数据采集器 —— 独立、自包含的 OpenTelemetry 自动插桩，为 jiuwenclaw / openjiuwen 多通道 AI Agent 采集 traces + metrics，经 OTLP 导出至任意标准可观测后端（Arize Phoenix / Langfuse / 自托管 labubu，三者同讲 OTLP，仅端点不同）。

## 使用（无侵入）

```bash
pip install jiuwenswarm-instrumentor          # 1. 装（非 editable，自带自动加载钩子）
# 2. 设环境变量（五个都要；漏 TRACES_EXPORTER 或 PROTOCOL 会没数据）
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http        # 4318 是 HTTP 端口，默认 grpc 会协议不匹配
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # 后端（Phoenix/Langfuse/labubu）
python -m jiuwenclaw.app                       # 3. 跑——父 + agentserver + gateway 子进程全自动插桩
```

> **Windows 用户**：上面 `export` 是 bash 语法，cmd/PowerShell 不认。cmd 用 `set OTEL_ENABLED=true`，PowerShell 用 `$env:OTEL_ENABLED="true"`，**设完在同一窗口立刻启动**（`set`/`$env:` 只对当前窗口生效）。完整三平台写法见 `docs/observability-quickstart.md`。自检：`set OTEL_ENABLED`(cmd)/`echo $env:OTEL_ENABLED`(PS) 应非空。

**成功的判据是后端 UI 能查到 `service=jiuwenclaw` 的 trace**（`[instrumentor] active` 这行 INFO 在 `.pth` 自动加载时可能不显示，不影响上报）。无需改 jiuwenclaw 源码、无需 CLI 包裹：装包时随附的 `jiuwenswarm_instrumentor.pth` 让每个 Python 进程（含 `app.py` fork 的两个子进程）启动即自动激活。

> ⚠️ 两个必设项是踩过的坑：漏 `OTEL_TRACES_EXPORTER=otlp`（默认 `none` 不导出）、漏 `OTEL_EXPORTER_OTLP_PROTOCOL=http`（gRPC 打到 HTTP 端口 4318 导出失败）——两个都设上才有数据。

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
| `OTEL_LOG_MESSAGES` | `false` | 设 `true` 采集完整 prompt/response(`gen_ai.input.messages`/`gen_ai.output.messages`)+ tool 参数/结果。**默认 false(隐私)** —— 不设则 trace 里看不到输入输出 |
| `OTEL_MESSAGE_CONTENT_MAX_LENGTH` | `4096` | 单条消息内容截断长度(字符) |

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
