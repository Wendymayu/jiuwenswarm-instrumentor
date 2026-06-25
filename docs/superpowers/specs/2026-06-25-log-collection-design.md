# jiuwenswarm-instrumentor 日志采集 设计规格

- **日期**: 2026-06-25
- **状态**: Draft — 待用户评审
- **参考**: [OTel Logs 数据模型](https://opentelemetry.io/docs/specs/otel/logs/data-model/)、[OTel Python SDK `_logs`](https://opentelemetry.io/docs/languages/python/)、labubu `docs/superpowers/specs/2026-06-06-otlp-logs-design.md`(`POST /v1/logs` + `LogRecord` schema + trace 关联)。

---

## 1. 背景与目标

jiuwenswarm-instrumentor 已实现 **traces + metrics** 两信号。本规格补上第三信号 **logs**:把 jiuwenclaw 应用自身的 stdlib `logging` 日志桥接到 OTel logs,经 OTLP 导出到 labubu(或 Phoenix / Langfuse),并与 trace 关联(同 `trace_id`)。

### 为什么做
- OTel logs 的核心价值是 **trace 关联**:日志挂在产生它的 trace 下,后端可直接 `GET /api/v1/logs/:traceId` 查看一条请求的全部日志。labubu 已是一等 OTLP logs 后端(`POST /v1/logs` + UI Logs 页 + trace 关联日志视图)。
- jiuwenclaw 的运营日志(网关路由、agent_client、工具执行、各通道)目前只落本地轮转文件;导出到后端后可统一检索、按 severity/event/trace 过滤。

### 关键约束
- **自包含,不依赖 `jiuwenclaw.telemetry`**(沿用本包原则)。允许 fail-soft 引用 `jiuwenclaw.utils.setup_logger`(应用 utils,非 telemetry 模块)。
- **v1 范围:只采 jiuwenclaw 的 stdlib 日志**。openjiuwen 自带 logging 抽象(stdlib 或 loguru 后端),其 loguru 后端不走 stdlib,桥接收不到 —— 作为独立子项目后续再做。
- **零新依赖**:OTel 1.43.0 的 `opentelemetry.sdk._logs` + OTLP `_log_exporter` 已在 venv;不引入 `opentelemetry-instrumentation-logging`(手写 handler 更好控属性映射 + 直接挂 `jiuwenclaw` logger)。

---

## 2. 范围

### 做
- stdlib `logging` → OTel `LogRecord` 桥接(手写 `OTelLogHandler`)。
- 直接挂载到 `logging.getLogger("jiuwenclaw")`(因该 logger `propagate=False`)。
- trace 关联:每条 LogRecord 带当前活跃 span 的 `trace_id`/`span_id`/`trace_flags`。
- severity 映射(stdlib levelno → OTel SeverityNumber/SeverityText)。
- `extra` 字段 → OTel attributes;`event.name` 提取(仅当 `extra` 显式带)。
- 复用 jiuwenclaw 自有 `logging.Filter` 做脱敏(piggyback);拿不到 filter 时回退 WARNING-only。
- `OTEL_LOGS_*` 环境配置;OTLP gRPC/HTTP + console 导出。

### 不做(非目标)
- openjiuwen 日志(尤其 loguru 后端)—— 独立子项目。
- 自定义脱敏规则配置(复用应用现有 filter 即可)。
- 日志采样/限流(用 `BatchLogRecordProcessor` + 级别过滤控量足够)。
- 文件 tail 方式(丢 trace 关联,不做)。

---

## 3. 设计原则

- **fail-soft**:handler `emit` 永不抛(→ `self.handleError` + debug log);provider/导出器/import/patch 失败均降级,不影响应用日志与运行。
- **trace 关联优先**:核心卖点;handler `emit` 内读 `trace.get_current_span()`。
- **镜像现有模式**:provider 层设全局 proxy(`logs.set_logger_provider`),instrumentor 层用 `opentelemetry._logs.get_logger` proxy 拿 logger;instrumentor 接受注入 `otel_logger` 参数便于单测用 fake。
- **幂等**:handler 打标记,挂载/重挂前查 `logger.handlers`。
- **可关停**:`OTEL_ENABLED`(总)+ `OTEL_LOGS_EXPORTER=none`(信号级)。

---

## 4. 架构

```
jiuwenclaw 应用代码  logger.info/warning/error(...)  (stdlib logging)
        │
        ▼  (record 沿 jiuwenclaw.* logger 树传播到 "jiuwenclaw" logger)
logging.getLogger("jiuwenclaw")  ──addHandler──►  OTelLogHandler (我们挂的)
        │                                              │  emit(record):
        │                                              │   ├─ body = getMessage() (+exc traceback), 截断
        │                                              │   ├─ severity = levelno → SeverityNumber/Text
        │                                              │   ├─ trace_id/span_id/flags = get_current_span()
        │                                              │   ├─ attributes = extra 字段 + code.* + log.logger + ...
               │                                              │   └─ event.name = extra.get(event_name/...)
        ▼                                              ▼
OTelLogHandler.otel_logger.emit(LogRecord(...))
        │
        ▼
LoggerProvider → BatchLogRecordProcessor → OTLPLogExporter (http /v1/logs | grpc)
        │
        ▼
labubu  →  logs 表 (trace_id, span_id, severity, event_name, body, attributes)
           UI: /logs 列表 + 全文/severity/event 过滤;trace 详情下关联日志 (GET /api/v1/logs/:traceId)
```

### 组件清单

| 文件 | 改动 |
|---|---|
| `config.py` | `InstrumentorConfig` 增 `logs_*` 字段;`load_config()` 读 `OTEL_LOGS_*` |
| `provider.py` | `init_providers` 增 logs 分支:`LoggerProvider`(共享 resource) + `BatchLogRecordProcessor` + OTLPLogExporter;`logs.set_logger_provider(lp)`(fail-soft) |
| `instrumentors/logs.py`(**新**) | `OTelLogHandler(logging.Handler)` + `instrument_logs(...)` |
| `instrumentors/__init__.py` | `apply_instrumentors` 加 `("logs", ...)` 步,`cfg.logs_exporter != "none"` 时调用 |
| `activate.py` | 启动日志加 `logs=%s`;无签名变化 |
| `tests/instrumentors/test_logs.py`(**新**) | in-memory 导出器 + 注入 logger 的单测 |

---

## 5. 组件设计

### 5.1 `config.py`

`InstrumentorConfig` 增字段:

```python
logs_exporter: str = "none"          # otlp | console | none
logs_endpoint: str = "http://localhost:4317"
logs_protocol: str = "grpc"          # grpc | http
logs_headers: dict = None
log_level: str = "INFO"              # NOTSET|DEBUG|INFO|WARNING|ERROR|CRITICAL
log_excluded_loggers: tuple = ()     # logger 名称子串
log_message_max_length: int = 8192
```

`load_config()` 读取(镜像 traces/metrics 的 endpoint/protocol/headers 继承逻辑):

| 变量 | 默认 | 说明 |
|---|---|---|
| `OTEL_LOGS_EXPORTER` | `none` | `otlp`/`console`/`none` |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | `OTEL_EXPORTER_OTLP_ENDPOINT` | labubu `http://localhost:4318` |
| `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc`/`http` |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | base headers 合并 | |
| `OTEL_LOGS_LEVEL` | `INFO`(回退读 `OTEL_LOG_LEVEL`) | 采集级别 |
| `OTEL_LOGS_EXCLUDED_LOGGERS` | `jiuwenclaw.interface.resp` | 逗号分隔名称子串 |
| `OTEL_LOG_MESSAGE_MAX_LENGTH` | `8192` | body/属性值截断 |

### 5.2 `provider.py`

`init_providers` 在 traces/metrics 之后增 logs 分支(共享同一 `resource`):

```python
if cfg.logs_exporter != "none":
    lp = LoggerProvider(resource=resource)
    if cfg.logs_exporter == "otlp":
        lp.add_log_record_processor(BatchLogRecordProcessor(_otlp_log_exporter(cfg)))
    elif cfg.logs_exporter == "console":
        lp.add_log_record_processor(SimpleLogRecordProcessor(ConsoleLogExporter()))
    try:
        logs.set_logger_provider(lp)
    except Exception:
        pass  # already set
```

导出器(注意路径是 `_log_exporter` 单数,与 traces 的 `trace_exporter` 不同):

```python
def _otlp_log_exporter(cfg):
    if cfg.logs_protocol == "http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        return OTLPLogExporter(endpoint=f"{cfg.logs_endpoint}/v1/logs", headers=cfg.logs_headers)
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    return OTLPLogExporter(endpoint=cfg.logs_endpoint, headers=cfg.logs_headers)
```

### 5.3 `instrumentors/logs.py` —— `OTelLogHandler`

`logging.Handler` 子类,`emit(record)` 把 stdlib `LogRecord` 翻译成 OTel `LogRecord` 并经 `otel_logger.emit(...)` 发出。**永不抛异常**。

```python
class OTelLogHandler(logging.Handler):
    def __init__(self, otel_logger, *, level="INFO", excluded_loggers=(), message_max_length=8192):
        super().__init__(level=_level_to_stdlib(level))
        self._otel_logger = otel_logger
        self._excluded = tuple(excluded_loggers)
        self._max_len = message_max_length

    def emit(self, record):
        try:
            if any(s in record.name for s in self._excluded):
                return
            attrs = _record_to_attributes(record, self._max_len)
            event_name = _extract_event_name(record)
            if event_name:
                # labubu 的 OTLP proto (v0.20.0) 无原生 event_name 字段,从 attributes["event.name"] 提取;
                # 同时设原生 event_name 字段,兼容 Phoenix 等新协议后端。
                attrs["event.name"] = event_name
            kwargs = dict(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=get_current(),  # SDK 据此设 trace_id/span_id/trace_flags
                severity_text=_severity_text(record.levelno),
                severity_number=_severity_number(record.levelno),
                body=_cap(_format_body(record), self._max_len),
                attributes=attrs,
            )
            if event_name:
                kwargs["event_name"] = event_name
            self._otel_logger.emit(LogRecord(**kwargs))
        except Exception:
            self.handleError(record)
```

#### 映射细节

- **body**:`record.getMessage()`;`record.exc_info` 时追加 `logging.Formatter().formatException(record.exc_info)`;按 `message_max_length` 截断。
- **severity 映射**(OTel SeverityNumber 规范区间:TRACE 1-4 / DEBUG 5-8 / INFO 9-12 / WARN 13-16 / ERROR 17-20 / FATAL 21-24)。**SeverityText 用 OTel 规范串**(TRACE/DEBUG/INFO/WARN/ERROR/FATAL),**不用 stdlib `record.levelname`**(它是 `WARNING`/`CRITICAL`,与 labubu 的过滤枚举 `WARN`/`FATAL` 不匹配)。`_severity_text(levelno)` 与 `_severity_number(levelno)` 一起按 levelno 查表:

  | stdlib levelno | SeverityNumber | SeverityText |
  |---|---|---|
  | 50 (CRITICAL) | 21 | FATAL |
  | 40 (ERROR) | 17 | ERROR |
  | 30 (WARNING) | 13 | WARN |
  | 20 (INFO) | 9 | INFO |
  | 10 (DEBUG) | 5 | DEBUG |
  | 0 (NOTSET) | 9 | INFO |

  labubu 在 `translateLogs` 里按 SeverityNumber 归一到 `WARN`/`FATAL` 等(见其 `storage.go` `severityFromNumber`),SeverityText 也用规范串即可对齐。
- **trace 关联**:LogRecord 构造时传 `context=get_current()`(OTel 当前 context,携带活跃 span);SDK 据此自动设 `trace_id`/`span_id`/`trace_flags`(直接传 `trace_id=`/`span_id=` 已废弃)。agent/LLM/tool 执行期间的日志(在 `jiuwenclaw.agent.invoke` 等当前 span 内)关联;trace 外日志(网关路由前等)`get_current_span(context)` 返回无效 span → trace_id 为 None,作为独立日志入库。
- **attributes**(`_record_to_attributes`):从 `record.__dict__` 取非标准键(`user_visible`/`host`/`port` 等 `extra` 字段),stringify + 截断;并加 `code.function`/`code.filepath`/`code.lineno`/`log.logger`/`thread.id`/`process.id`。stdlib 内部键(name/msg/args/levelno/levelname/created/.../message)排除。
- **`event.name`**(`_extract_event_name`):仅当 `record.__dict__` 含 `event_name`/`event.name`/`event` 时取值 → 填 `event.name` 属性(labubu 提升为 `event_name` 列)。jiuwenclaw 的 `user_visible` 是可见性标签,不强转。

### 5.4 `instrumentors/logs.py` —— `instrument_logs`

```python
def instrument_logs(otel_logger=None, *, level="INFO", excluded_loggers=(), message_max_length=8192):
    if otel_logger is None:
        from opentelemetry._logs import get_logger
        otel_logger = get_logger("jiuwenswarm_instrumentor.logs")
    handler = OTelLogHandler(otel_logger, level=level,
                             excluded_loggers=excluded_loggers,
                             message_max_length=message_max_length)
    handler._jiuwenswarm_otel = True  # 幂等标记

    def attach():
        loggr = logging.getLogger("jiuwenclaw")
        if any(getattr(h, "_jiuwenswarm_otel", False) for h in loggr.handlers):
            return  # 已挂
        # 复用应用现有 filter 做脱敏
        copied = _copy_filters_from(loggr, handler)
        if not copied:
            handler.setLevel(max(_level_to_stdlib(level), logging.WARNING))  # 回退 WARNING-only
        else:
            handler.setLevel(_level_to_stdlib(level))
        loggr.addHandler(handler)

    attach()
    _patch_setup_logger_to_reattach(attach)  # fail-soft
```

#### 挂载 + 脱敏策略(解两个坑)

- **`propagate=False`**:直接 `addHandler` 到 `logging.getLogger("jiuwenclaw")`(不挂 stdlib root)。子 logger(`jiuwenclaw.app` 等)默认向 `jiuwenclaw` 传播 → 一处挂载覆盖整棵树。
- **`setup_logger()` 导入时清空 handler**:`_patch_setup_logger_to_reattach(attach)` fail-soft import `jiuwenclaw.utils`,把 `setup_logger` 包一层 —— 原函数跑完(清空 + 重装文件/控制台 handler)后调 `attach()` 重挂。import 失败/属性不存在则跳过(只挂一次,fail-soft)。activation 早于 app_agentserver import jiuwenclaw.utils,故 patch 先就位,后续 import 触发 setup_logger 时自动重挂。
- **filter 复用**(`_copy_filters_from`):从 `jiuwenclaw` logger 已有 handler 的 `.filters` 拷到我们的 handler → 复用 `SensitiveDataFilter` 等,脱敏一致。**回退**:一个 filter 都没拷到 → handler 级别抬到 `max(level, WARNING)`(无脱敏保证时不发 INFO);拷到了用配置级别(默认 INFO)。
- **幂等**:挂载前查 `logger.handlers` 是否已有带 `_jiuwenswarm_otel` 标记的;`setup_logger` 清空后 handler 不在列表 → 允许重挂(不会重复)。

### 5.5 `instrumentors/__init__.py` + `activate.py`

`apply_instrumentors` 加一步(`cfg.logs_exporter != "none"` 时):

```python
("logs", lambda: logs.instrument_logs(
    level=cfg.log_level,
    excluded_loggers=cfg.log_excluded_loggers,
    message_max_length=cfg.log_message_max_length)),
```

`activate()` 启动日志:`"[instrumentor] active: traces=%s metrics=%s logs=%s endpoint=%s"`。无签名变化(logs 用 `opentelemetry._logs` 全局 proxy)。

---

## 6. 错误处理

全 fail-soft(与现有模式一致):
- `OTelLogHandler.emit`:try/except → `self.handleError(record)` + `logger.debug`,**永不外抛**(不影响应用日志路径)。
- `init_providers` logs 分支:构造/`set_logger_provider` 失败 → `logger.exception`,不设 provider。
- `instrument_logs`:import `jiuwenclaw.utils` / patch `setup_logger` / `attach` 失败 → 跳过相应步骤,不抛。
- 导出器失败(后端不可达):`BatchLogRecordProcessor` 内部重试/丢弃,不影响 emit。

---

## 7. 测试(`tests/instrumentors/test_logs.py`)

镜像现有 fake + in-memory 风格:per-test 构造 `LoggerProvider` + `InMemoryLogRecordExporter`(经 `SimpleLogRecordProcessor`),把 `provider.get_logger("...")` 注入 `instrument_logs(otel_logger=...)`;不设全局 proxy 避免污染其他测试。

用例:
1. `test_emits_record_with_severity_and_body` —— INFO/WARN/ERROR → SeverityNumber/Text + body 正确。
2. `test_trace_correlation` —— `start_as_current_span` 内发日志 → LogRecord.trace_id == span trace_id;span 外 → None。
3. `test_extra_attributes` —— `extra={user_visible, host}` → 属性保留。
4. `test_event_name` —— `extra={event_name}` → `event.name` 填充。
5. `test_excluded_logger` —— `jiuwenclaw.interface.resp` 不发。
6. `test_filter_piggyback` —— 在 `jiuwenclaw` logger 已有 handler 上挂假脱敏 filter(改写 message)→ OTel body 是脱敏后的;handler 复制了该 filter。
7. `test_no_filters_warning_fallback` —— 无 filter → handler 级别抬到 WARNING;INFO 不发、WARN 发。
8. `test_setup_logger_reattach` —— 注入 fake `jiuwenclaw.utils` 模块,其 `setup_logger` 清空 handlers → patch 后调用 → handler 重挂。
9. `test_emit_never_raises` —— `otel_logger.emit` 抛异常 → handler 吞掉,不外抛。
10. `test_idempotent` —— `instrument_logs` 两次 → `jiuwenclaw` logger 只一个带标记 handler。

---

## 8. 验收

- `OTEL_LOGS_EXPORTER=otlp OTEL_LOGS_LEVEL=INFO` 启动 agentserver/gateway,发一条对话。
- labubu UI `/logs` 出现 jiuwenclaw 日志,severity/body 正确,可按 severity/event/trace 过滤。
- trace 详情下(labubu `GET /api/v1/logs/:traceId`)出现该请求执行期间的关联日志。
- `console` 导出器本地可见 LogRecord。
- 关 `OTEL_LOGS_EXPORTER`(或 `OTEL_ENABLED=false`)→ 零成本 no-op,应用日志不受影响。
- 单测全绿(新增 10 例,总 46)。
