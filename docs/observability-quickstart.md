# jiuwenswarm 可观测快速上手

三步跑起来。后端任选:Arize Phoenix / Langfuse / 自托管 labubu(同讲 OTLP,只改 endpoint)。

## 1. 装包

```bash
pip install jiuwenswarm-instrumentor
```

> 用 Python 3.11–3.13(如 `py -3.13`)。必须是**非 editable** 安装才会装上自动加载钩子;`pip install -e .` 不行。

## 2. 设环境变量

**默认值已经够用**(traces/metrics exporter=`otlp`、protocol=`grpc`、endpoint=`http://localhost:4317`、`log_messages=true` 采集完整 prompt/response)。后端在本地 4317 gRPC(labubu / Phoenix 默认)的话,**只设一个变量**:

> ⚠️ **enterprise_dev 双开关**:jiuwenswarm 内置可观测模块读 `OTEL_ENABLED`,本独立探针读 `OTEL_INSTRUMENTOR_ENABLED`——两者分离,避免同时安装时数据重复上报。要用本探针就**只设 `OTEL_INSTRUMENTOR_ENABLED=true`**(并保持 `OTEL_ENABLED` 未设/为 false,内置模块即 no-op);要用内置就反过来。等内置模块移除后,本探针会改回读 `OTEL_ENABLED`,恢复单开关。

**Windows cmd:**
```cmd
set OTEL_INSTRUMENTOR_ENABLED=true
```

**Windows PowerShell:**
```powershell
$env:OTEL_INSTRUMENTOR_ENABLED="true"
```

**Linux / macOS / git-bash:**
```bash
export OTEL_INSTRUMENTOR_ENABLED=true
```

后端不在本地 4317、或用 HTTP(如 Langfuse 云端),加 endpoint/protocol:

```cmd
set OTEL_EXPORTER_OTLP_PROTOCOL=http
set OTEL_EXPORTER_OTLP_ENDPOINT=https://your-backend
```

> ⚠️ **设完在同一窗口立刻启动(第 3 步)** —— `set`/`$env:`/`export` 只对当前 shell 生效,关掉窗口或换窗口就没了。持久化用 `setx VAR value`(cmd,重启窗口生效)或系统环境变量。
>
> 🔒 **默认采集完整 prompt/response + tool 参数/结果**(`OTEL_LOG_MESSAGES` 默认 `true`)。生产或隐私敏感场景设 `OTEL_LOG_MESSAGES=false` 关掉,只保留 token 用量/模型等指标。
>
> **自检变量真设上了**(启动前,同一窗口):cmd `set OTEL_INSTRUMENTOR_ENABLED` / PowerShell `echo $env:OTEL_INSTRUMENTOR_ENABLED` / bash `echo $OTEL_INSTRUMENTOR_ENABLED` → 应非空。

> 旧的 0.1.x 默认 exporter=`none`、`log_messages=false`,要显式设 5 个变量才有数据;0.2.0 起默认开箱即用。

本地不想接后端?设 `OTEL_TRACES_EXPORTER=console`(可同时 `OTEL_METRICS_EXPORTER=console`),span 直接打到终端。

## 3. 启动

```bash
python -m jiuwenclaw.app
```

子进程(agentserver + gateway)启动时各自自动激活。**成功的判据是后端 UI 里能查到 `service=jiuwenclaw` 的 trace** —— 后端收到就是真的在上报。

> 日志里**可能**出现 `[instrumentor] active: traces=otlp metrics=otlp ...`(若 jiuwenclaw 的日志配置捕获了 instrumentor logger);但 `.pth` 自动加载发生在解释器启动、日志配置之前,这行 INFO 有时不显示——**它出不出现都不影响上报**,以**后端有数据**为准。发一条对话,在后端按 `service=jiuwenclaw` 查,应看到 `jiuwenclaw.gateway.agent.request` / `jiuwenclaw.agent.invoke` / `gen_ai.chat` / `gen_ai.tool` 等 span。

---

## 4. 其余环境变量

第 2 步只设了 `OTEL_INSTRUMENTOR_ENABLED`(其余默认开箱即用)。按需覆盖:

| 变量 | 默认 | 说明 |
|---|---|---|
| `OTEL_INSTRUMENTOR_ENABLED` | `false` | **本探针总开关**(enterprise_dev)。关闭时零开销 no-op |
| `OTEL_ENABLED` | `false` | jiuwenclaw **内置**可观测模块的总开关(本探针不读它)。内置移除后本探针将改回读它 |
| `OTEL_TRACES_EXPORTER` | `otlp` | `otlp`(发后端)/ `console`(打终端)/ `none`(不导出) |
| `OTEL_METRICS_EXPORTER` | `otlp` | 同上,metrics 通道 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc`(4317)/ `http`(4318),与端口匹配。用 HTTP 改 `http` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | 后端地址。非本地或 HTTP 后端时改 |
| `OTEL_SERVICE_NAME` | `jiuwenclaw` | 服务名,挂在每个 span 上 |
| `OTEL_LOG_MESSAGES` | `true` | 默认采完整 prompt/response(`gen_ai.input.messages`/`gen_ai.output.messages`)+ tool 参数/结果。**隐私敏感设 `false` 关掉** |
| `OTEL_MESSAGE_CONTENT_MAX_LENGTH` | `4096` | 单条消息/工具内容字符截断阈值。设 `0` / `none` / `off` 关闭截断(采集完整内容,体积更大)。日志体见 `OTEL_LOG_MESSAGE_MAX_LENGTH`(默认 `8192`,同样支持 `0`) |
| `OTEL_LOGS_EXPORTER` | `otlp` | jiuwenclaw stdlib 日志:`otlp`/`console`/`none`。默认开,trace 页可看执行期日志;量太大或不要日志设 `none` |
| `OTEL_LOG_MESSAGE_MAX_LENGTH` | `8192` | 单条日志体字符截断阈值;`0`/`none`/`off` 关闭截断 |
| `OTEL_LOGS_LEVEL` | `INFO` | 日志级别(`DEBUG` 爆量) |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 鉴权头,逗号分隔 `k=v`(如 Langfuse `Authorization=Basic ...`) |
| `JIUWENSWARM_INSTRUMENT_AUTOLOAD` | (未设) | 设 `false` 关掉自动激活,改用 `jiuwen-instrument your_app`(单进程入口) |
| `OTEL_INSTRUMENT_GATEWAY` | `false` | 是否采集 gateway 数据(`channel.request`/`jiuwenclaw.gateway.agent.request` span + traceparent 注入)。默认关:与 agent 行为无关,`agent.invoke` 直接作为根 trace。想看 gateway 或要跨进程链路时设 `true` |

`OTEL_INSTRUMENTOR_ENABLED=false`(默认)时本探针零开销 no-op,不影响业务。

