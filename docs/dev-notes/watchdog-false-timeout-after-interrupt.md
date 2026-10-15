---
name: watchdog-false-timeout-after-interrupt
description: "中断+切会话+恢复后出现\"后端 60s 无响应\"是前端看门狗僵尸误报，后端其实在正常流式输出"
type: project
---


「后端 60s 无响应，可中止并检查运行日志」是**前端误报**：ChatConsole handleSend 每次调用都会建自己的 60s 看门狗（lastEventAt + setInterval），但清理只发生在三个终态监听器（onFinal/onError/onAborted）或 sendCleanup 里。

漏清理链（2026-09-05 实测，会话 A 的 turn 被中止、用户在会话 B 发过消息后切回 A）：
1. handleSend 开头无条件 `cleanupListeners()`（ChatConsole.tsx:3943）会退订**上一次**调用（unsubsRef 只存最新一次）的监听器——跨会话误杀：B 的发消息把 A 的在途监听器退订了。
2. 被中止的 A 调用：`await sendPromise; settleLifecycle()`（:5044）成功后**不调 sendCleanup**——终态事件因监听器已被退订（或 onAborted 的 turn_id 严格守卫 :4933 因 activeTurnIdRef 是跨会话全局 ref 而丢弃）无人处理 → 看门狗 interval 永不清理。
3. 切回 A 时 load() 会 merge 后 `inFlightCacheRef.delete(sessionKey)`（:3320）——看门狗的「近期缓存事件也算活跃」补偿失效，lastEventAt 冻结在监听器被退订前的最后一个心跳（~10:41:03）。
4. 用户回到 A 后（currentSessionRef === sendSessionKey），冻结满 60s → 僵尸看门狗把假警告 append 进消息列表；新回合自己的 warnMsgId 为 null，不会撤销旧消息 → 警告永久残留。后端侧此时每 10s 心跳 + 持续 reasoning_delta，完全健康（日志可证）。

**Why:** 中断/切会话/设置页来回切换很容易凑齐这套条件，用户感知为"恢复执行后后端超时"。
**How to apply:** 已按 PR #947 修复（develop 合入前）：handleSend 成功路径只 clearWatchdogTimer + 自退订（不能 sendCleanup——onFinal 已排定 reveal RAF，会冻结最终回复）；4 个 IPC 处理器按 routingKey（= chat.send 的 key，线程会话时 ≠ sendSessionKey）丢弃外来会话事件；sendInvocationRegistryRef 注册表让 unmount 全量释放所有活跃调用；cleanupListeners(onlyMine) 身份限定。相关内存 [confirm-card-issue-714-fix](confirm-card-issue-714-fix.md)。

排查日志位置：`<用户主目录>\.miqi\workspace\logs\bridge-YYYY-MM-DD.log`（MIQI_HOME=.miqi；部分行时间戳是 UTC 带 Z，部分是本地时间带 Z，注意换算）。bridge 进程「stdin closed → graceful shutdown (0 active chat tasks)」= 主进程文件监听器触发的延迟重启，正常现象。
