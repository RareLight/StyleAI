---
trigger: always_on
---

**NEVER use native `pcall` in Lightroom Classic Plugins.**

Always use `LrTasks.pcall` instead, which is designed to handle yielding across C-boundaries.

### Why this is critical:
Lightroom's SDK relies heavily on asynchronous tasks and yielding (especially during teardown sequences, like `doneFunc`). In Lua 5.1, yielding across a C-call boundary (such as the native `pcall` function) throws a fatal `"attempt to yield across C-call boundary"` error. 

Because `pcall` swallows errors silently, using it to wrap Lightroom callbacks (like shutdown hooks) will cause the callback to crash silently. Lightroom will then hang indefinitely waiting for a completion signal that will never arrive.