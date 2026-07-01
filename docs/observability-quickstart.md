# jiuwenswarm 可观测快速上手

三步跑起来。后端任选:Arize Phoenix / Langfuse / 自托管 labubu(同讲 OTLP,只改 endpoint)。

## 1. 装包

```bash
pip install jiuwenswarm-instrumentor
```

> 用 Python 3.11–3.13(如 `py -3.13`)。必须是**非 editable** 安装才会装上自动加载钩子;`pip install -e .` 不行。

## 2. 设环境变量

五个都要设(漏 `OTEL_TRACES_EXPORTER` 或 `OTEL_EXPORTER_OTLP_PROTOCOL` 会没数据,见下方说明)。**Windows 用户注意:`export` 是 bash 语法,cmd/PowerShell 里要用各自的写法**,否则变量设不上、`jiuwenclaw-start` 在无 OTEL 环境下跑 → 没数据。

**Windows cmd:**
```cmd
set OTEL_ENABLED=true
set OTEL_TRACES_EXPORTER=otlp
set OTEL_METRICS_EXPORTER=otlp
set OTEL_EXPORTER_OTLP_PROTOCOL=http
set OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

**Windows PowerShell:**
```powershell
$env:OTEL_ENABLED="true"
$env:OTEL_TRACES_EXPORTER="otlp"
$env:OTEL_METRICS_EXPORTER="otlp"
$env:OTEL_EXPORTER_OTLP_PROTOCOL="http"
$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
```

**Linux / macOS / git-bash:**
```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

> ⚠️ **设完在同一窗口立刻启动(第 3 步)** —— `set`/`$env:`/`export` 只对当前 shell 生效,关掉窗口或换窗口就没了。要持久化用 `setx VAR value`(cmd,重启窗口生效)或系统环境变量设置。
>
> ⚠️ 两个"必设"是踩过的坑:漏 `OTEL_TRACES_EXPORTER=otlp` → activate 会跑、类会被 patch,但 span 不导出(默认 `none`);漏 `OTEL_EXPORTER_OTLP_PROTOCOL=http` → gRPC exporter 打到 HTTP 端口 4318,导出失败。两个都设上才有数据。

**自检变量是否真设上了**(在启动 jiuwenclaw-start 的同一窗口、启动前敲):
- cmd:`set OTEL_ENABLED` → 应显示 `OTEL_ENABLED=true`
- PowerShell:`echo $env:OTEL_ENABLED` → 应输出 `True`
- bash:`echo $OTEL_ENABLED` → 应输出 `true`

输出为空 = 没设上,这就是没数据的根因。本地不想接后端就把两个 exporter 都设成 `console`,span 直接打到终端(此时 endpoint 可不设)。

## 3. 启动

```bash
python -m jiuwenclaw.app
```

子进程(agentserver + gateway)启动时各自自动激活。**成功的判据是后端 UI 里能查到 `service=jiuwenclaw` 的 trace** —— 后端收到就是真的在上报。

> 日志里**可能**出现 `[instrumentor] active: traces=otlp metrics=otlp ...`(若 jiuwenclaw 的日志配置捕获了 instrumentor logger);但 `.pth` 自动加载发生在解释器启动、日志配置之前,这行 INFO 有时不显示——**它出不出现都不影响上报**,以**后端有数据**为准。发一条对话,在后端按 `service=jiuwenclaw` 查,应看到 `jiuwenclaw.gateway.agent.request` / `jiuwenclaw.agent.invoke` / `gen_ai.chat` / `gen_ai.tool` 等 span。

---

## 4. 其余环境变量

第 2 步已经设了上报必需的五个(`OTEL_ENABLED` / `OTEL_TRACES_EXPORTER` / `OTEL_METRICS_EXPORTER` / `OTEL_EXPORTER_OTLP_PROTOCOL` / `OTEL_EXPORTER_OTLP_ENDPOINT`),其余按需加:

| 变量 | 默认 | 说明 |
|---|---|---|
| `OTEL_TRACES_EXPORTER` | `none` | `otlp`(发后端)/ `console`(打终端)/ `none` |
| `OTEL_METRICS_EXPORTER` | `none` | 同上,metrics 通道 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc`(4317)/ `http`(4318),与端口匹配 |
| `OTEL_SERVICE_NAME` | `jiuwenclaw` | 服务名,挂在每个 span 上 |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 鉴权头,逗号分隔 `k=v`(如 Langfuse `Authorization=Basic ...`) |
| `JIUWENSWARM_INSTRUMENT_AUTOLOAD` | (未设) | 设 `false` 关掉自动激活,改用 `jiuwen-instrument your_app`(单进程入口) |

`OTEL_ENABLED=false`(默认)时全部零开销 no-op,不影响业务。

