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
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # 你的后端地址
```

本地先验证不想接后端?加一行 `export OTEL_TRACES_EXPORTER=console`,span 直接打到终端。

## 3. 启动

```bash
python -m jiuwenclaw.app
```

日志出现这行即成功(父 + agentserver + gateway 子进程各一行,都会自动激活):

```
INFO:jiuwenswarm_instrumentor:[instrumentor] active: ...
```

完事。span 已发到后端。

---

## 4. 其余环境变量

上面三步只用了 `OTEL_ENABLED` 和 `OTEL_EXPORTER_OTLP_ENDPOINT`,其余按需加:

| 变量 | 默认 | 说明 |
|---|---|---|
| `OTEL_TRACES_EXPORTER` | `none` | `otlp`(发后端)/ `console`(打终端)/ `none` |
| `OTEL_METRICS_EXPORTER` | `none` | 同上,metrics 通道 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc`(4317)/ `http`(4318),与端口匹配 |
| `OTEL_SERVICE_NAME` | `jiuwenclaw` | 服务名,挂在每个 span 上 |
| `OTEL_EXPORTER_OTLP_HEADERS` | - | 鉴权头,逗号分隔 `k=v`(如 Langfuse `Authorization=Basic ...`) |
| `JIUWENSWARM_INSTRUMENT_AUTOLOAD` | (未设) | 设 `false` 关掉自动激活,改用 `jiuwen-instrument your_app`(单进程入口) |

`OTEL_ENABLED=false`(默认)时全部零开销 no-op,不影响业务。

