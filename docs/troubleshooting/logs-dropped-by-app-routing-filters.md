# 日志采集:应用路由 filter 把 jiuwenclaw 日志全丢掉的问题

> 日期: 2026-06-25 · 分支: `feat/instrumentor-impl` · 涉及文件: `src/jiuwenswarm_instrumentor/instrumentors/logs.py`
> 实测环境: jiuwenswarm `resume_enterprise_dev` + `.venv_resume_enterprise_dev`,labubu(`POST /v1/logs` :4318,UI :8080)

## 问题现象

启动 agentserver + gateway(`OTEL_LOGS_EXPORTER=otlp OTEL_LOGS_LEVEL=INFO`),`[instrumentor] active: traces=otlp metrics=otlp logs=otlp` 正常打印,但 labubu 的 **Logs 页面看不到任何 jiuwenclaw 日志**(`GET /api/v1/logs` 返回 `total:0`)。traces + metrics 都正常上报,唯独 logs 全空。

诡异点:**gateway 的日志能上报,agentserver 的不能**——两者用同一份 instrumentor、同一套 env、同一个 `jiuwen-instrument.exe` 启动。

## 原因

### 主因:filter 复用把"路由 filter"也拷了过来,而 stdlib `Handler.handle` 遇 filter 返回 `False` 就跳过 `emit`

`OTelLogHandler` 的设计(见 spec §5.4)是**复用 jiuwenclaw 自有的 `logging.Filter`**(脱敏),从 `logging.getLogger("jiuwenclaw")` 已有 handler 上把 filter 拷到我们的 handler。`_copy_filters_from` 拷的是**所有 handler 的 filter 的并集**(实测 17 个),其中包括:

- `SensitiveDataFilter` —— **脱敏型**(mutate record,返回 `True` 保留)—— 我们想要的。
- `IdentityFieldFilter` / `UserVisibleTagFilter` / `_ComponentNameFilter` / `_CompositeFilter` —— **路由型**:决定记录该去哪个文件/handler,对"不属于本 handler 的记录"返回 `False`(拒绝)。

而 stdlib `logging.Handler.handle` 的实现是:`self.filter(record)` 跑所有 filter,**任一返回 `False` 就跳过 `emit`**。我们的 handler 拿了这 17 个 filter 的并集,比任何一个单独的 app handler 都严——大多数 jiuwenclaw 日志(缺少特定 component/identity 上下文的)都被某个路由 filter 拒掉,`emit` 根本没被调用 → 0 条 LogRecord 产出。

**为什么 gateway 能上报、agentserver 不能**:两者 handler 都挂上了、都拷了 filter,但 gateway 的日志体量更大(WS 连接、channel 派发等高频 INFO),恰好有少量记录能"漏网"通过所有 17 个 filter;agentserver 的启动日志几乎全被路由 filter 拦下。所以 gateway 有日志、agentserver 看似没有(实际是都被拒了)。

### 辅因:labubu 每 5min 清理无 trace 关联的日志

labubu 的 Purge 逻辑(`ALTER TABLE logs DELETE WHERE trace_id NOT IN (SELECT trace_id FROM traces)`):**trace_id 不在 traces 表里的日志会被定时清理**。agentserver 的**启动日志**都在 span 之外(`trace_id=0`),不属于任何 trace → 每 5min 被清掉。所以即便上报了,5min 后也查不到——加剧了"看不到日志"的错觉。**chat 消息期间的日志**(在 `agent.invoke` span 内,带 trace_id)才会被 labubu 关联保留。

## 验证(实测定位过程)

1. **handler 挂上了吗?** 用 `OTEL_LOGS_EXPORTER=console` 重启 agentserver → 控制台打出 **84 条 OTel LogRecord**(jiuwenclaw 启动日志)。✅ handler 挂载正常、`emit` 被调用、无 `handleError`。
2. **OTLP 导出通吗?** 诊断脚本 `setup()` + `force_flush()` + 简单日志 → labubu 收到(`POST /v1/logs 200`)。✅ 导出链路本身没坏。
3. **那为什么 agentserver 的 OTLP 出不去?** 诊断脚本对比:
   - **17 个 filter 在**:发 `jl.info("TEST_BARE_INFO")` → OTel 控制台**无 LogRecord**(被 filter 拒了),但 jiuwenclaw 自己的 StreamHandler 却打印了它(说明 StreamHandler 的 filter 子集没拒,我们 handler 的 17 个并集拒了)。
   - **清空 filter**(`oh.filters.clear()`)后再发 → OTel 控制台**打出 LogRecord**(INFO + WARN 都有)。
   - → 铁证:**filter 在就拒,filter 清空就出**。
4. **复杂属性不是问题**:发带 `extra={user_visible, component, host, port, request_id}` 的日志 + `force_flush` → labubu 收到。排除"labubu 拒收复杂属性"假设。
5. **labubu 5min 清理**:同一批诊断日志,5min 后再查 → 没了(trace_id=0 被清)。chat 消息期间的日志(trace_id 非零)则保留。

## 解决

重写 `OTelLogHandler.handle`:**filter 只为副作用(脱敏)跑一遍,但不让它们拒绝记录**——我们要抓全部 jiuwenclaw 日志,不该被 app 的路由 filter 拦下。

```python
def handle(self, record):
    # 排除的 logger 早返回(不跑 filter 副作用)
    if any(s in record.name for s in self._excluded):
        return False
    # filter 只为副作用跑(如 SensitiveDataFilter 脱敏),不让路由/组件 filter 拒绝记录
    # —— stdlib Handler.handle 遇任一 filter 返回 False 就跳过 emit,会把大部分 app 日志丢掉
    for f in self.filters:
        try:
            f.filter(record)
        except Exception:
            pass
    self.acquire()
    try:
        self.emit(record)
    finally:
        self.release()
    return True
```

(原 `emit` 里的 excluded 检查移到 `handle` 里,在跑 filter 之前早返回。)

**效果**:agentserver 启动后,labubu 立刻收到其 jiuwenclaw 日志(实测 `[AgentServer] Workspace already initialized` 等,logger=`jiuwenclaw.utils`,数千条)。`SensitiveDataFilter` 仍跑(脱敏不丢),路由 filter 的"拒绝"被绕过(我们要全量)。

单测 `test_rejecting_filter_does_not_drop_log`:挂一个 `return False` 的 filter,断言日志仍被采集(旧代码会 0 条,新代码 1 条)。

## 权衡

- **脱敏保留**:`SensitiveDataFilter`(mutate + 返回 True)仍跑,敏感字段照样脱敏。
- **路由 filter 的"拒绝"被绕过**:这是我们想要的——app 的路由 filter 是为"哪个文件/handler 收哪条记录"设计的,对 OTel 全量采集无意义;我们要抓所有 `jiuwenclaw.*` 日志。
- **路由 filter 的副作用**:个别路由 filter 可能在 `filter()` 里给 record 加属性(如 component),这些副作用仍会跑——基本无害(顶多给 OTel LogRecord 多几个属性)。
- **不依赖 stdlib `Handler.handle` 的 filter 门控**:我们自己的 `handle` 全权决定是否 `emit`,stdlib 的"filter 拒即跳过"不再适用。

## 相关

- **另一个也会导致"0 日志"的缺陷(已在最终评审修掉)**:`_patch_setup_logger_to_reattach` 里 `import jiuwenclaw.utils` 会触发 `setup_logger()`(utils.py 模块尾调用)在 patch 安装**之前**跑(unwrap 状态),把我们的 handler 清掉,且 `app_agentserver` 不会再调 `setup_logger` → handler 整个进程都没了。修复:`instrument_logs` 末尾再加一次 `attach()`(import 跑完 setup_logger 清空后重挂)。单测 `test_setup_logger_at_import_time_reattaches`(用 `sys.meta_path` finder 模拟 import 时触发 setup_logger)。详见 spec §5.4 + `logs.py` 注释。
- **labubu 5min 清理无 trace 日志**:不是 instrumentor 的 bug,是 labubu 的保留策略。排查"日志看不到"时,要么在 5min 内查,要么发 chat 消息让日志带 trace_id(被关联保留)。
- 单测:`tests/instrumentors/test_logs.py`(`test_rejecting_filter_does_not_drop_log` + `test_filter_piggyback` + `test_setup_logger_at_import_time_reattaches`)。
- 启动指南:`docs/guides/start-jiuwenswarm-with-instrumentor.md` §4 常见坑(logs 相关)。
