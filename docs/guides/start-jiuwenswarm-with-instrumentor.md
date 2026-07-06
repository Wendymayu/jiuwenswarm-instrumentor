# jiuwenswarm-instrumentor 探针使用指南（enterprise_dev + develop）

> 两个分支的探针代码分叉、版本不撞：**enterprise_dev = 0.3.x（jiuwenclaw 应用）**，**develop = 1.1.x（jiuwenswarm 应用）**。本文按分支分两节，分别讲 setup / 环境变量 / 启动 / 验证 / 排错。
>
> 后端任选：Arize Phoenix / Langfuse / 自托管 labubu（同讲 OTLP，只改 endpoint）。下文以 labubu HTTP `:4318` 为例；gRPC 后端把 `OTEL_EXPORTER_OTLP_PROTOCOL` 留默认 grpc、endpoint 指到 `:4317`。
>
> 机器默认 `python`/`pip` 是 3.14，本包 `requires-python <3.14`，一律用 **Python 3.13**（`py -3.13` 或各 venv 的 `python.exe`）。

---

# A. enterprise_dev（jiuwenswarm-instrumentor 0.3.x，jiuwenclaw 应用）

## A.0 前置

- **jiuwenswarm 仓库**：`D:/opensource/gitcode/jiuwenclaw`（enterprise / deep_agent 架构）。
- **venv**：`.venv_enterprise_dev`（Python 3.13）。
- **数据目录**：`~/.jiuwenclaw/`（config 在 `~/.jiuwenclaw/config/`，`.env` 在 `~/.jiuwenclaw/config/.env`）。
- **instrumentor 仓库**：`D:/opensource/github/jiuwenswarm-instrumentor`，分支 `enterprise_dev`。
- **可观测后端**：labubu（HTTP `:4318` / UI `:8080` 或 `:3001`），或 Phoenix / Langfuse。

## A.1 装探针

**非 editable（推荐，带 `.pth` 自动加载钩子，split-process 一条命令搞定）**

```bash
cd D:/opensource/gitcode/jiuwenclaw
.venv_enterprise_dev/Scripts/python.exe -m pip install D:/opensource/github/jiuwenswarm-instrumentor
# 验证
.venv_enterprise_dev/Scripts/python.exe -c "import jiuwenswarm_instrumentor as j; print(j.__version__)"   # 0.3.0
ls .venv_enterprise_dev/Lib/site-packages/ | grep jiuwenswarm_instrumentor.pth   # 应存在
```

editable 安装不发货 `.pth`，autoload 不生效，要走 A.4 的两终端 CLI 方式。一般用非 editable。

## A.2 打 TelemetryRail 补丁（**必须**，避免双写）

jiuwenswarm enterprise_dev 内置 `jiuwenclaw/telemetry/` 模块。`init_telemetry()` 认 `OTEL_ENABLED`（留空即 no-op），但 **`TelemetryRail` 在 `agentserver/deep_agent/interface_deep.py` 和 `agentserver/tools/subagent_executor/executor.py` 里单独实例化，不检查 `OTEL_ENABLED`**，会借探针设的 tracer 产 span，导致每个 `agent.invoke` / `gen_ai.chat` 出现两份。

**补丁（一行，未提交，留在 jiuwenswarm 工作树）**：

`jiuwenclaw/telemetry/instrumentors/telemetry_rail.py` 的 `TelemetryRail.__init__` 里：
```python
self._degraded: bool = False      # ← 改成 True
```
改后所有 rail hook 进 `if self._degraded: return None` 直接 no-op。

> 等 jiuwenswarm 正式删除 `telemetry/` 模块后，此补丁连同文件一起消失，无需回退。`git pull`/切分支可能覆盖，留意保留。

## A.3 环境变量

探针总开关是 **`OTEL_INSTRUMENTOR_ENABLED`**（enterprise_dev 专属，与内置的 `OTEL_ENABLED` 分离）。**不要设 `OTEL_ENABLED`**（留空即内置 off）。

`~/.jiuwenclaw/config/.env`：
```bash
OTEL_INSTRUMENTOR_ENABLED=true          # 探针总开关（唯一必设）
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # labubu / Phoenix / Langfuse
OTEL_SERVICE_NAME=jiuwenclaw
OTEL_LOG_MESSAGES=true                  # 采完整 prompt/response + tool 参数/结果（隐私敏感设 false）
OTEL_LOGS_EXPORTER=otlp                 # 采 jiuwenclaw stdlib 日志
OTEL_LOGS_LEVEL=INFO
# 可选：OTEL_MESSAGE_CONTENT_MAX_LENGTH=0   # 关闭消息截断，采完整内容（默认 4096）
```

> 探针的 `.pth` autoload 会在 app main 之前加载这个 `.env`，所以写进 `.env` 最省事，不用每次 shell `set`。

## A.4 启动

**一条命令（推荐，用 A.1 非 editable 的 .pth 自动加载）**
```bash
cd D:/opensource/gitcode/jiuwenclaw
.venv_enterprise_dev/Scripts/python.exe -m jiuwenclaw.app
```
`app.py` fork `app_agentserver` + `app_gateway` 子进程，各自启动时 `.pth` 自动跑 `_autoload` → `activate()`，早于 agent 构造。父 + 两子各自向 labubu 发 span。

**两终端（editable 安装或想单独看某进程日志时）**
```bash
# 终端 1 AgentServer
.venv_enterprise_dev/Scripts/jiuwen-instrument.exe jiuwenclaw.app_agentserver
# 终端 2 Gateway（AgentServer 起来后再起）
.venv_enterprise_dev/Scripts/jiuwen-instrument.exe jiuwenclaw.app_gateway
```
> 不要用 `jiuwen-instrument jiuwenclaw.app` 一条命令——CLI 只包裹父进程，fork 的子进程不被插桩。一条命令搞定靠 `.pth` 自动加载。

## A.5 验证

1. AgentServer 日志出现 `[instrumentor] active: traces=otlp metrics=otlp endpoint=http://localhost:4318` + `[AgentServer] ready`。
2. Gateway 日志出现 `[App] connected to AgentServer`。
3. 发一条对话消息。
4. labubu UI 查 `service=jiuwenclaw` 的 trace，应看到（scope = `jiuwenswarm_instrumentor`）：
   - `jiuwenswarm.agent.invoke`（`gen_ai.agent.name`、`jiuwenswarm.session.id`、`gen_ai.input.messages` 用户输入）
   - `gen_ai.chat`（`gen_ai.system`、`request.model`、`usage.input/output/total_tokens`、`streaming.first_token_ms`、`gen_ai.input.messages`/`gen_ai.output.messages`/`gen_ai.tool.definitions`、`gen_ai.context.*` token 归因）
   - `gen_ai.tool`（`gen_ai.tool.name`/`call.id`、`tool.arguments`/`result`；skill 工具带 `gen_ai.operation.name=load_skill/release_skill` + `skill.loaded`/`skill.released` 事件）
   - `jiuwenswarm.session.create` / `.end`
5. metrics：`gen_ai.client.token.usage`、`gen_ai.client.operation.duration`、`gen_ai.tool.count`/`duration`、`gen_ai.agent.duration`、`gen_ai.skill.call.*`。
6. logs：labubu Logs 页可见 jiuwenclaw 日志；trace 详情下显示执行期关联日志（带 trace_id）。

## A.6 排错（enterprise_dev）

- **每个 span 出现两份**：`TelemetryRail` 补丁没打（A.2），或 `OTEL_ENABLED=true` 被你设了（把内置也开了）。确认 rail 的 `_degraded=True` 且 `OTEL_ENABLED` 未设。
- **trace 里 scope 不是 `jiuwenswarm_instrumentor`**：那是内置 rail 的 span——同上，rail 补丁没生效。
- **探针完全没数据**：`OTEL_INSTRUMENTOR_ENABLED` 没设（探针在 enterprise_dev 不认 `OTEL_ENABLED`）。确认 `.env` 在 `~/.jiuwenclaw/config/.env`（不是 `~/.jiuwenswarm`）。
- **`gen_ai.tool` 嵌在 `gen_ai.chat` 下**：流式工具执行 + streaming span 持 context 的副作用，已修（streaming 用 `start_span` 非当前）。详见 `docs/troubleshooting/streaming-tool-span-parentage.md`。
- **第一次 LLM 调用没文本输出**：tool_call 响应（模型决定调工具），正常。

---

# B. develop（jiuwenswarm-instrumentor 1.1.x，jiuwenswarm 应用）

## B.0 前置

- **jiuwenswarm 仓库**：`D:/opensource/gitcode/jiuwenclaw`，develop 分支（包名 `jiuwenswarm`）。
- **venv**：`.venv_develop`（Python 3.13）。
- **数据目录**：`~/.jiuwenswarm/`（`.env` 在 `~/.jiuwenswarm/config/.env`）。
- **instrumentor 仓库**：`D:/opensource/github/jiuwenswarm-instrumentor`，分支 `develop`。
- **可观测后端**：同上。

## B.1 装探针

```bash
cd D:/opensource/gitcode/jiuwenclaw
.venv_develop/Scripts/python.exe -m pip install D:/opensource/github/jiuwenswarm-instrumentor
.venv_develop/Scripts/python.exe -c "import jiuwenswarm_instrumentor as j; print(j.__version__)"   # 1.1.0
```

## B.2 环境变量

探针总开关是 **`OTEL_ENABLED`**（develop 单开关，无内置 telemetry 冲突）。**不需要 rail 补丁**。

`~/.jiuwenswarm/config/.env`：
```bash
OTEL_ENABLED=true                       # 探针总开关（唯一必设）
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_SERVICE_NAME=jiuwenswarm
OTEL_LOG_MESSAGES=true
OTEL_LOGS_EXPORTER=otlp
OTEL_LOGS_LEVEL=INFO
# 可选：OTEL_MESSAGE_CONTENT_MAX_LENGTH=0
```

## B.3 启动

```bash
cd D:/opensource/gitcode/jiuwenclaw
.venv_develop/Scripts/python.exe -m jiuwenswarm.app
```
子进程由 `.pth` autoload 自动覆盖。两终端 CLI 替代：
```bash
.venv_develop/Scripts/jiuwen-instrument.exe jiuwenswarm.server.app_agentserver
.venv_develop/Scripts/jiuwen-instrument.exe jiuwenswarm.gateway.app_gateway
```

## B.4 验证

labubu 查 `service=jiuwenswarm` 的 trace，span 同 A.5 第 4 条（`jiuwenswarm.agent.invoke` / `gen_ai.chat` / `gen_ai.tool` / `jiuwenswarm.session.*`）。

## B.5 排错（develop）

- **探针没数据**：`OTEL_ENABLED` 没设（develop 认 `OTEL_ENABLED`，不认 `OTEL_INSTRUMENTOR_ENABLED`）。确认 `.env` 在 `~/.jiuwenswarm/config/.env`。
- **editable 安装后不自动激活**：editable 不发货 `.pth`，用 B.3 两终端 CLI，或非 editable 重装。
- 注意 instrumentor 仓库的分支要和 venv 对应：venv_develop 配 develop 分支探针，venv_enterprise_dev 配 enterprise_dev 分支探针——错配会开关不认（develop 探针读 `OTEL_ENABLED`，enterprise_dev 探针读 `OTEL_INSTRUMENTOR_ENABLED`）。

---

# C. 通用排错

- **`Overriding of current TracerProvider is not allowed` 警告**：某进程里 activate 跑了两次（如 `.pth` autoload + 手动 `setup()`）。无害，第一次设的 provider 生效。
- **labubu Logs 页日志稀疏/被清**：`trace_id=0` 的日志 labubu 每 5min 清。执行期日志应带 trace_id（在 span 内发出）；启动/心跳类无 span 的日志会被清，正常。
- **后端 4318 连不上**：确认 labubu 起着（`curl http://localhost:8080/api/v1/services` 应含 service）。gRPC 后端用 4317、protocol 留 grpc。
- **API 调试**：`GET http://localhost:8080/api/v1/services`、`GET /api/v1/traces?service=<svc>`、`GET /api/v1/traces/{trace_id}`、`GET /api/v1/logs/{trace_id}`。
