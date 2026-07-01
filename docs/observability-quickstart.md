# jiuwenswarm 可观测快速上手

三步跑起来。后端任选:Arize Phoenix / Langfuse / 自托管 labubu(同讲 OTLP,只改 endpoint)。

## 1. 装包

```bash
pip install jiuwenswarm-instrumentor
```

> 用 Python 3.11–3.13(如 `py -3.13`)。必须是**非 editable** 安装才会装上自动加载钩子;`pip install -e .` 不行。

## 2. 设环境变量

```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp           # 必设!默认 none 不导出
export OTEL_METRICS_EXPORTER=otlp          # metrics 同理
export OTEL_EXPORTER_OTLP_PROTOCOL=http    # 必设!4318 是 HTTP 端口,默认 grpc 会打到 4317 协议不匹配
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # 你的后端地址
```

> ⚠️ 上面两个"必设"是踩过的坑:漏 `OTEL_TRACES_EXPORTER=otlp` → activate 会跑、类会被 patch,但 span 不导出(默认 `none`);漏 `OTEL_EXPORTER_OTLP_PROTOCOL=http` → gRPC exporter 打到 HTTP 端口 4318,导出失败。两个都设上才有数据。

本地先验证不想接后端?把两个 exporter 都设成 `console`,span 直接打到终端(此时 endpoint 可不设)。

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

