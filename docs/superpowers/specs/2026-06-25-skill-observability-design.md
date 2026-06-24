# jiuwenswarm-instrumentor Skill 可观测采集 设计规格

- **日期**: 2026-06-25
- **状态**: Draft — 待用户评审
- **参考**: jiuwenswarm `docs/zh/OpenTelemetry可观测性.md` §"Skill 调用链追踪" + §5.1 skill 指标;[OTel GenAI #86](https://github.com/open-telemetry/semantic-conventions/issues/86);旧 `jiuwenclaw/telemetry/instrumentors/telemetry_rail.py` 的 skill 处理(参考实现,运行时**不依赖**)。

---

## 1. 背景与目标

jiuwenswarm 的可观测设计中,skill(技能)的可观测遵循 OTel GenAI #86 社区共识:**不为 skill 建独立 span,在工具执行 span 上设 skill 属性 + 事件**。skill 的加载(`skill_tool`)和释放(`skill_complete`)本身就是工具调用(走 `AbilityManager.execute_single`),而本 instrumentor 已经 wrap 了它(产出 `gen_ai.tool` span)。

本规格在现有 tool instrumentor 基础上,**补充 skill 专属的可观测数据**——skill 属性 + 生命周期事件 + skill 指标,使 trace/metric 后端(labubu / Phoenix / Langfuse)能看到 skill 的加载/释放、耗时、调用次数、错误。

### 关键约束
- 自包含,不依赖 `jiuwenclaw.telemetry` 或其扩展(沿用本包原则)。
- 不为 skill 建独立 span(遵循 #86)。
- 不做上下文 token 分布(`gen_ai.context.*`)、不做 `gen_ai.skill.token.usage`(需要"识别上下文里哪段是 skill body/pin",属上下文计算范畴,本期跳过,后续单独做)。
- 不带身份 labels(`jiuwenclaw.user.id` / `domain.id` / `app.id`,需 jiuwenclaw `IdentityStore`,跳过保持自包含)。
- tiktoken 不需要(本期无 token 计量)。

---

## 2. 范围

### 做
**Layer 1 — skill span 属性 + 事件**(在 `skill_tool` / `skill_complete` 的 `gen_ai.tool` span 上):
- `gen_ai.operation.name` = `load_skill`(skill_tool)/ `release_skill`(skill_complete)
- `gen_ai.skill.name`
- `gen_ai.skill.id` = `skill_<hash(skill_name) & 0xFFFFFFFF:08x>`(skill_tool 加载时设)
- 事件 `skill.loaded` {`skill.name`, `skill.path`}(skill_tool 加载 body 成功时)
- 事件 `skill.released` {`skill.name`}(skill_complete 时)

**Layer 2 — 3 个 skill 指标**:
- `gen_ai.skill.call.count`(Counter)—— skill_tool 调用次数
- `gen_ai.skill.duration`(Histogram)—— skill_tool 激活到 skill_complete 释放的时长
- `gen_ai.skill.error.count`(Counter)—— skill 执行错误次数

### 不做(非目标)
`gen_ai.context.*` token 分布、`gen_ai.skill.token.usage`、`gen_ai.skill.version`/`description`(无来源)、身份 labels、独立 skill span。

---

## 3. 设计原则

- **遵循 #86**:skill 属性挂在 tool span,不建 skill span。
- **fail-soft**:skill 属性/事件/指标/状态读写都 try/except,绝不阻断工具调用。
- **可关停**:沿用 `OTEL_ENABLED` 总开关(整个 instrumentor 的)。
- **跨 task 状态**:skill_tool 与 skill_complete 跑在不同 asyncio task,duration 状态用**模块级 dict**(非 ContextVar,ContextVar 跨不了这些 task)。

---

## 4. 架构

```
AbilityManager.execute_single(skill_tool / skill_complete)
   │  (我们已 wrap → gen_ai.tool span)
   ▼
instrumentors/tool.py: 检测 skill_tool / skill_complete
   ├── 设 Layer1 属性 + 事件(写在 gen_ai.tool span 上)
   ├── 记 Layer2 metric(metrics.py)
   └── 驱动 instrumentors/skill.py 的 duration 状态(record_load / pop_release)
instrumentors/session.py: 会话结束 → skill.clear_session(清孤儿)
```

---

## 5. 组件

### 5.1 `instrumentors/skill.py`(新)
会话级 duration 状态(模块级,跨 task 共享):
- `_skill_sessions: dict[tuple[str, str], float]` —— `(session_id, skill_name) -> start_monotonic`
- `record_load(session_id, skill_name)` —— 记 start
- `pop_release(session_id, skill_name) -> float | None` —— 取出 start(用于算 duration)
- `clear_session(session_id)` —— 清该 session 所有未配对项(孤儿清理)

### 5.2 `instrumentors/tool.py`(扩展)
`instrument_tool` 的 traced 函数里,在拿到结果后:
- 若 `tool_call.name == "skill_tool"`:读 skill_name(`tool_msg.metadata.skill_name`,当 `is_skill_body` / `original_is_skill_body` 为真;回退 `arguments.skill_name`)→ 设 `gen_ai.operation.name=load_skill`、`gen_ai.skill.name`、`gen_ai.skill.id`;发 `skill.loaded` {skill.name, skill.path=metadata.relative_file_path};记 `skill.call.count`;`skill.record_load(session_id, skill_name)`;`is_error` → `skill.error.count`。
- 若 `tool_call.name == "skill_complete"`:读 skill_name(`arguments.skill_name`)→ 设 `gen_ai.operation.name=release_skill`、`gen_ai.skill.name`;发 `skill.released` {skill.name};`skill.pop_release` 取 start → 记 `skill.duration`;`is_error` → `skill.error.count`。
- session_id 从 `session.get_session_id()`(execute_single 的 `session` 参数)取,回退 `current_request_attrs()`。

### 5.3 `metrics.py`(扩展)
`Metrics` 类加(fail-soft,沿用现有 `record_*` 模式):
- `_skill_call_count = meter.create_counter("gen_ai.skill.call.count", unit="{call}", ...)`
- `_skill_duration = meter.create_histogram("gen_ai.skill.duration", unit="s", ...)`
- `_skill_error_count = meter.create_counter("gen_ai.skill.error.count", unit="{call}", ...)`
- `record_skill_call(attrs)` / `record_skill_duration(seconds, attrs)` / `record_skill_error(attrs)`
- metric labels(attrs):`gen_ai.skill.name`、`gen_ai.system`(skill 指标固定为 `"jiuwenclaw"`)、`jiuwenclaw.channel.id`(从 `current_request_attrs()`)。不带 `gen_ai.skill.version`(无来源)。

### 5.4 `attributes.py`(扩展)
- `GEN_AI_SKILL_NAME = "gen_ai.skill.name"`
- `GEN_AI_SKILL_ID = "gen_ai.skill.id"`
- (`GEN_AI_OPERATION_NAME` 已有。`gen_ai.skill.version` 不记录——无来源,metric 也不带该 label。)

### 5.5 `instrumentors/session.py`(扩展)
instrument_session 的会话结束(`cleanup`)处调 `skill.clear_session(session_id)`。

---

## 6. 数据流

1. **skill_tool(加载)**:tool instrumentor 命中 → 读 skill_name + path(metadata)→ 设 load_skill 属性 + skill.id → 发 `skill.loaded` → 记 `skill.call.count` → `record_load` 记 start;`is_error` → `skill.error.count`。
2. **skill_complete(释放)**:命中 → 读 skill_name(arguments)→ 设 release_skill 属性 → 发 `skill.released` → `pop_release` 取 start → 记 `skill.duration`;`is_error` → `skill.error.count`。
3. **会话结束**:instrument_session → `clear_session(session_id)` 清掉没配对的 skill_tool 残留。

---

## 7. 数据来源(对照旧 `TelemetryRail` 已确认可得)

- **skill_tool**:`tool_msg.metadata.skill_name` + `is_skill_body` / `original_is_skill_body` + `relative_file_path`;回退 `tool_call.arguments.skill_name`。
- **skill_complete**:`tool_call.arguments.skill_name`(JSON parse)。
- **skill.id**:`skill_<hash(skill_name) & 0xFFFFFFFF:08x>`。
- **skill.version**:不设(无来源,跟设计文档一致)。
- **session_id**:`session.get_session_id()`(execute_single 的 `session` 参数)。

---

## 8. 错误处理

- 所有 skill 属性/事件/metric/状态操作 try/except,失败只 debug 日志,不抛、不阻断工具调用。
- `skill.py` 状态:模块 dict 访问;`clear_session` 幂等。
- 沿用 `OTEL_ENABLED=false` → instrumentor 全程 no-op。

---

## 9. 测试策略

- **单元**:fake `skill_tool` / `skill_complete` 调用(fake `tool_msg.metadata`:skill_name / is_skill_body / relative_file_path)→ 断言 `gen_ai.tool` span 上的 skill 属性 + `skill.loaded` / `skill.released` 事件 + 3 个 metric(call.count / duration / error.count)。
- **duration**:先 skill_tool(`record_load`)再 skill_complete(`pop_release`)→ 断言 `skill.duration` 记了且状态被 pop。
- **孤儿**:skill_tool 后未 skill_complete → `clear_session` 清掉残留。
- **fail-soft**:metadata 缺 skill_name 时不崩(属性略,metric 不记或记空)。

---

## 10. 项目布局(改动)

```
src/jiuwenswarm_instrumentor/
  instrumentors/skill.py        (新)
  instrumentors/tool.py         (扩展:skill 检测 + 属性/事件/metric)
  instrumentors/session.py      (扩展:clear_session)
  metrics.py                    (扩展:3 个 skill metric)
  attributes.py                 (扩展:3 个常量)
tests/instrumentors/test_skill.py  (新)
```

---

## 11. 风险

- **跨 task 状态**:skill_tool / skill_complete 跑在不同 asyncio task → duration 状态必须模块级(非 ContextVar)。已按此设计。
- **skill 内容识别(body/pin)本期不做**:留给后续"token 分布"spec,所以 `skill.token.usage` + `context.*` 不在本期。
- **`tool_msg.metadata` 字段名**(skill_name / is_skill_body / relative_file_path)来自旧实现对照;impl 时复核 openjiuwen 的 skill tool 实际返回结构(若字段名不同,按实际调整——属实现细节,不改设计)。
- **session_id 来源**:execute_single 的 `session` 参数需复核在 skill 调用路径下是否非 None;若 None,回退 `current_request_attrs()`。
