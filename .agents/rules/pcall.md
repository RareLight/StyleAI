---
trigger: always_on
---

**Use `LrTasks.pcall` for normal asynchronous tasks in Lightroom Classic Plugins.**

`LrTasks.pcall` is designed to handle yielding across C-boundaries. In Lua 5.1, yielding across a C-call boundary (such as the native `pcall` function) throws a fatal `"attempt to yield across C-call boundary"` error.

### The Teardown Exception (`doneFunc`):
You MUST use native `pcall(doneFunc)` during the Lightroom teardown sequence (e.g. inside `LrShutdownFunction`). 
During plugin teardown, the Lightroom asynchronous task scheduler becomes unreliable or is already suspended. Calling `LrTasks.pcall(doneFunc)` will cause the plugin to hang indefinitely because it waits on a scheduler that is no longer running. Because `doneFunc` is completely synchronous and never yields, native `pcall` is perfectly safe and required here.

**CRITICAL RULE**: Because the teardown sequence cannot use the async scheduler,
you MUST NEVER call `LrTasks.execute` inside a teardown hook (or any function
that yields). Prefer no I/O or process launch at all during teardown; the backend
owns bounded idle shutdown. If an explicitly approved teardown design ever
requires an OS call, only a demonstrably detached, bounded `os.execute()` call is
eligible and it must not delay `doneFunc`.
