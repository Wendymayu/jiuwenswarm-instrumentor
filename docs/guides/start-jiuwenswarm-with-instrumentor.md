# 启动 jiuwenswarm 并带上插桩上报可观测数据

> 本指南基于 `jiuwenswarm-instrumentor`(分支 `feat/instrumentor-impl`)在真实 jiuwenswarm 上跑通 trace + metric 上报到 labubu(或 Phoenix / Langfuse)的实测经验。

## 0. 前置条件

- **jiuwenswarm 仓库**:`D:/code/opensource/gitcode/jiuwenswarm`,分支 `resume_enterprise_dev`(enterprise / deep_agent 架构)。
- **venv**:`.venv_enterprise_dev`(Python 3.13)。⚠️ 本机默认 `python`/`pip` 是 3.14,被本包 `requires-python = ">=3.11,<3.14"` 拒绝 —— 一律用 `py -3.13` 或该 venv 的 `python.exe`。
- **可观测后端**:labubu(本机已起,gRPC `:4317` / HTTP `:4318` / UI `:8080` / 前端 `:3001`),或 Phoenix / Langfuse(协议一致,只改 endpoint)。
- **instrumentor 仓库**:`D:/code/opensource/github/jiuwenswarm-instrumentor`(分支 `feat/instrumentor-impl`)。

## 1. 一次性 setup(三个动作,均未提交到 jiuwenswarm)

### 1.1 把 instrumentor 装进 jiuwenclaw 的 venv(editable)

```bash
cd D:/code/opensource/gitcode/jiuwenswarm
.venv_enterprise_dev/Scripts/python.exe -m pip install -e D:/code/opensource/github/jiuwenswarm-instrumentor
# 验证
.venv_enterprise_dev/Scripts/python.exe -c "import jiuwenswarm_instrumentor as j; print(j.__version__)"
# 应输出 0.1.0
```

editable 安装 → 之后改 instrumentor 源码无需重装,重启进程即生效。

### 1.2 禁用 jiuwenclaw 自带的旧 telemetry(避免双写)

jiuwenclaw 内置的 `jiuwenclaw/telemetry/` 还在、且会被 `app_agentserver` / `app_gateway` 调用。它会和我们的 instrumentor **同时插桩**(重复 span)。要在 jiuwenswarm 工作树里改 3 处(未提交):

- `jiuwenclaw/app_agentserver.py` 第 114 行:`init_telemetry()` → 注释掉。
- `jiuwenclaw/app_gateway.py` 第 880 行:`init_telemetry()` → 注释掉。
- `jiuwenclaw/telemetry/instrumentors/telemetry_rail.py` 第 291 行:`self._degraded: bool = False` → 改成 `True`(让 TelemetryRail 所有 hook no-op;rail 是在 `interface_deep` / `subagent_executor` 里独立实例化的,注释 `init_telemetry` 不够,必须把 rail 自身 no-op)。

> 注:我们的 instrumentor 在 `jiuwen-instrument` 激活时会先 set OTel provider,旧 `init_telemetry` 的 `set_tracer_provider` 会失败(被覆盖),所以**我们的 exporter 生效**;但旧 rail 仍会产出重复 span,所以才需要 1.3 的 `_degraded=True`。等 jiuwenclaw 正式删除 telemetry 模块后,这三处改动就不需要了。

### 1.3 修 web 前端一个语法错误(否则聊天 WS hook 崩)

`jiuwenclaw/web_enterprise/src/hooks/useWebSocket.ts` 第 742 行有个多余的 `}`(过早闭合回调),vite 转译会报 `Expected ")" but found "const"`。删掉第 742 行那个多余的 `}`(未提交)。

```bash
# 验证修复后能转译
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/src/hooks/useWebSocket.ts  # 200 = OK
```

## 2. 启动(三个后台进程)

### 2.1 环境变量(三个进程共用)

```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # labubu / Phoenix / Langfuse
export OTEL_SERVICE_NAME=jiuwenclaw
export OTEL_LOG_MESSAGES=true   # 记录完整 prompt/response + tool 参数/结果(隐私敏感,生产可关)
export OTEL_LOGS_EXPORTER=otlp              # 采集 jiuwenclaw stdlib 日志
export OTEL_LOGS_LEVEL=INFO                 # 采集级别 (DEBUG 会爆量)
# 可选: export OTEL_LOGS_EXCLUDED_LOGGERS=jiuwenclaw.interface.resp
```

### 2.2 AgentServer(终端 1,被插桩)

```bash
cd D:/code/opensource/gitcode/jiuwenswarm
.venv_enterprise_dev/Scripts/jiuwen-instrument.exe jiuwenclaw.app_agentserver
```

### 2.3 Gateway(终端 2,被插桩,AgentServer 起来后再起)

```bash
cd D:/code/opensource/gitcode/jiuwenswarm
.venv_enterprise_dev/Scripts/jiuwen-instrument.exe jiuwenclaw.app_gateway
```

### 2.4 Web 前端(终端 3,可选,用于浏览器聊天)

```bash
cd D:/code/opensource/gitcode/jiuwenswarm/jiuwenclaw/web_enterprise
npm run dev   # vite,默认 :5173
```

> ⚠️ **不能用 `jiuwen-instrument jiuwenclaw-app` 一条命令**:app.py 会用裸 `sys.executable` fork agentserver/gateway 子进程,子进程不被插桩。所以拆成两条 `jiuwen-instrument` 直接起 agentserver + gateway。要一条命令搞定,得在 venv 装 `sitecustomize` 自动激活(会给 venv 加启动钩子,需你授权;见下"附录")。

## 3. 验证

1. AgentServer 日志出现 `[instrumentor] active: traces=otlp metrics=otlp endpoint=http://localhost:4318` + `[AgentServer] ready: ws://127.0.0.1:18092`。
2. Gateway 日志出现 `[App] connected to AgentServer: ws://127.0.0.1:18092`。
3. 浏览器开 `http://localhost:5173`,发一条对话消息。
4. labubu UI(`http://localhost:3001` 或 `:8080`)里查 `service=jiuwenclaw` 的 trace。

### 应看到的 span(我们的,scope = `jiuwenswarm_instrumentor`)

| span | 属性/事件 |
|---|---|
| `jiuwenclaw.agent.invoke` | `gen_ai.agent.name`、`jiuwenclaw.session.id` |
| `gen_ai.chat` | `gen_ai.system`/`request.model`/`usage.input_tokens`/`output_tokens`/`total_tokens`、`streaming.first_token_ms`(流式)、`response.finish_reason`、`gen_ai.input.messages`(输入消息 JSON)、`gen_ai.output.messages`(输出/含 tool_calls)、`gen_ai.tool.definitions`(可选工具列表) |
| `gen_ai.tool` | `gen_ai.tool.name`/`call.id`、`gen_ai.tool.arguments`/`result`(OTEL_LOG_MESSAGES=true);skill 工具额外带 `gen_ai.operation.name=load_skill/release_skill`、`gen_ai.skill.name`、`gen_ai.skill.id` + `skill.loaded`/`skill.released` 事件 |
| `jiuwenclaw.session.create` / `jiuwenclaw.session.end` | `jiuwenclaw.session.id` |

### metrics

`gen_ai.client.token.usage`、`gen_ai.client.operation.duration`、`gen_ai.tool.count`/`duration`、`gen_ai.agent.duration`、`gen_ai.skill.call.count`/`duration`/`error.count`。

### logs(新)

labubu UI 左侧 **Logs** 页(`/logs`):可见 jiuwenclaw 日志,按 severity / event_name / trace_id 过滤,body 全文搜索。
打开任一 trace,其详情下显示该请求执行期间的关联日志(labubu `GET /api/v1/logs/:traceId`)。
agent/LLM/tool 执行期间的日志带 trace_id(挂在 trace 下);网关路由前等日志无 trace_id,作为独立日志入库。

> 排查 API:`GET http://localhost:8080/api/v1/services`(应含 `jiuwenclaw`)、`GET http://localhost:8080/api/v1/traces?service=jiuwenclaw`、`GET http://localhost:8080/api/v1/traces/{trace_id}`(注意:含消息内容的 trace,labubu 的 API JSON 可能因 JSON-string 属性转义而解析失败,但 UI 能正常显示;详见 `docs/troubleshooting/streaming-tool-span-parentage.md`)。

## 4. 常见坑

- **重复 span(每个 gen_ai.chat 出现两次)**:旧 TelemetryRail 没禁干净。确认 1.2 的三处改动都在(尤其 `telemetry_rail.py` 的 `_degraded=True`)。
- **`gen_ai.tool` 嵌在 `gen_ai.chat` 下面**:流式工具执行 + streaming span 持 context 的副作用。已修(streaming 路径用 `start_span` 非当前)。详见 `docs/troubleshooting/streaming-tool-span-parentage.md`。
- **第一次 LLM 调用没输出**:那是 tool_call 响应(模型决定调工具,无文本)。已修(按 index 累积 tool_call 增量作为输出)。
- **`jiuwen-instrument jiuwenclaw.app_agentserver` 报 `unrecognized arguments`**:已修(CLI argv 剥离模块名)。确认 instrumentor 是最新版(editable)。
- **trace 里 scope 不是 `jiuwenswarm_instrumentor`**:那是旧 telemetry 的 span(旧 rail 没 `_degraded=True`)。
- **labubu Logs 页没数据**:确认 `OTEL_LOGS_EXPORTER=otlp`(默认 `none` 不采);确认 labubu `POST /v1/logs` 可达(`curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:4318/v1/logs` 应 200)。`OTEL_LOGS_LEVEL=DEBUG` 会爆量,默认 INFO。
- **日志里没 prompt 等敏感字段被脱敏**:正常 —— instrumentor 复用了 jiuwenclaw 自有的 `SensitiveDataFilter`(从 `jiuwenclaw` logger 已有 handler 复制);若 jiuwenclaw 没装 filter,instrumentor 回退到 WARNING-only(不发 INFO)。

## 附录:sitecustomize 自动激活(一条命令启动,需授权)

在 `.venv_enterprise_dev/Lib/site-packages/sitecustomize.py` 放(给 venv 加启动钩子,会被分类器标记为持久化,需你明确授权):

```python
import os
if os.getenv("OTEL_ENABLED", "").strip().lower() in ("true", "1", "yes"):
    try:
        from jiuwenswarm_instrumentor import setup
        setup()
    except Exception:
        pass
```

之后直接 `jiuwenclaw-start app`(或 `jiuwenclaw-app`)即可,所有 fork 出的子进程都会自动插桩。`OTEL_ENABLED` 未设时是 no-op,不影响日常使用。去掉这个文件即可关闭自动激活。

---

> 所有 jiuwenswarm 侧改动(`app_agentserver.py` / `app_gateway.py` / `telemetry_rail.py` / `useWebSocket.ts`)均为 `resume_enterprise_dev` 工作树的**未提交**改动,自行决定保留/提交/回退。instrumentor 侧改动都在 `D:/code/opensource/github/jiuwenswarm-instrumentor` 的 `feat/instrumentor-impl` 分支(已提交,36 tests)。
