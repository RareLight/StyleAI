---
trigger: always_on
---

# Lightroom error-boundary rule

Use `LrTasks.pcall` for normal asynchronous Lightroom work because Lua 5.1
cannot yield across native `pcall` C boundaries.

The sole exception is `LrShutdownFunction`: call its synchronous `doneFunc`
with native `pcall`. Lightroom's task scheduler may already be suspended during
teardown, so `LrTasks.pcall` can hang. The shutdown hook must not perform HTTP,
filesystem, logging, task, or process-launch work and must never call
`LrTasks.execute`; backend idle shutdown and recovery handle service cleanup.
