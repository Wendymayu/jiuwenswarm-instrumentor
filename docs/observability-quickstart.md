# jiuwenswarm-instrumentor 探针快速上手

两个分支的探针代码分叉（版本不撞）：**enterprise_dev = 0.x / jiuwenclaw 应用**，**develop = 1.x / jiuwenswarm 应用**。用法不同，分开记。

后端任选：Arize Phoenix / Langfuse / 自托管 labubu（同讲 OTLP，只改 endpoint）。下文以 labubu HTTP `:4318` 为例。

---

## enterprise_dev（jiuwenswarm-instrumentor 0.3.x，jiuwenclaw 应用）

### 关键差异

- 探针总开关是 **`OTEL_INSTRUMENTOR_ENABLED`**（不是 `OTEL_ENABLED`）。`OTEL_ENABLED` 归 jiuwenclaw 内置 telemetry，**别设它**（留空即内置 off）。
- jiuwenclaw 内置的 `TelemetryRail` **不认 `OTEL_ENABLED`**，会在 agent 执行路径里单独产 span 导致双写。**必须打一个补丁**：`jiuwenclaw/telemetry/instrumentors/telemetry_rail.py` 里 `TelemetryRail.__init__` 的 `self._degraded: bool = False` 改成 `True`（让所有 rail hook no-op）。详见下方 guide。
- venv：`.venv_enterprise_dev`；数据目录 `~/.jiuwenclaw/`（`.env` 在 `~/.jiuwenclaw/config/.env`）。

### 三步

```bash
# 1. 装探针（非 editable，带 .pth 自动加载，split-process 子进程也覆盖）
.venv_enterprise_dev/Scripts/python.exe -m pip install D:/opensource/github/jiuwenswarm-instrumentor

# 2. 打 rail 补丁（见上，一行 False→True）+ 设环境变量
#    ~/.jiuwenclaw/config/.env 写入：
#      OTEL_INSTRUMENTOR_ENABLED=true
#      OTEL_TRACES_EXPORTER=otlp
#      OTEL_METRICS_EXPORTER=otlp
#      OTEL_EXPORTER_OTLP_PROTOCOL=http
#      OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
#      OTEL_LOG_MESSAGES=true

# 3. 跑
.venv_enterprise_dev/Scripts/python.exe -m jiuwenclaw.app
```

`app.py` fork 的 agentserver/gateway 子进程启动时各自跑 `.pth` autoload → 自动激活，无需 CLI 包裹。

---

## develop（jiuwenswarm-instrumentor 1.1.x，jiuwenswarm 应用）

### 关键差异

- 探针总开关是 **`OTEL_ENABLED`**（单开关，无内置 telemetry 冲突）。
- **不需要 rail 补丁**（develop 侧没有内置 TelemetryRail 双写问题）。
- venv：`.venv_develop`；数据目录 `~/.jiuwenswarm/`（`.env` 在 `~/.jiuwenswarm/config/.env`）。

### 三步

```bash
# 1. 装探针（非 editable，带 .pth 自动加载）
.venv_develop/Scripts/python.exe -m pip install D:/opensource/github/jiuwenswarm-instrumentor

# 2. 设环境变量
#    ~/.jiuwenswarm/config/.env 写入：
#      OTEL_ENABLED=true
#      OTEL_TRACES_EXPORTER=otlp
#      OTEL_METRICS_EXPORTER=otlp
#      OTEL_EXPORTER_OTLP_PROTOCOL=http
#      OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
#      OTEL_LOG_MESSAGES=true

# 3. 跑
.venv_develop/Scripts/python.exe -m jiuwenswarm.app
```

子进程同样由 `.pth` autoload 自动覆盖。

---

## 通用

- **Windows**：`export` 是 bash 语法。cmd 用 `set VAR=value`，PowerShell 用 `$env:VAR="value"`，**设完在同一窗口立刻启动**。写进 `.env` 最省事（探针 `.pth` autoload 会先于 app main 加载它）。
- **成功判据**：后端 UI 查到 `service=jiuwenclaw`（enterprise_dev）或 `service=jiuwenswarm`（develop）的 trace，含 `jiuwenswarm.agent.invoke` / `gen_ai.chat` / `gen_ai.tool` span。
- **不想接后端**：`OTEL_TRACES_EXPORTER=console`，span 直接打终端。
- **关闭消息截断**（采完整 prompt/response）：`OTEL_MESSAGE_CONTENT_MAX_LENGTH=0`。
- **关闭自动加载**（改用 CLI/代码激活）：`JIUWENSWARM_INSTRUMENT_AUTOLOAD=false`。
- 详细 setup / 验证 / 排错见 `docs/guides/start-jiuwenswarm-with-instrumentor.md`。
