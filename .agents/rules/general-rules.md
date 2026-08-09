---
trigger: always_on
---

# StyleAI Agent Guardrails

`AGENTS.md` is authoritative. Keep this short summary synchronized with it.

- StyleAI is local-only: `127.0.0.1:19819`, one active Lightroom catalog per
  adjacent `styleai.db`, Ollama/LM Studio metadata only, and no cloud providers,
  API keys, remote backend, telemetry, or cross-catalog routing.
- Use `LrTasks.pcall` for normal asynchronous Lua work. Never yield inside a
  catalog write transaction. Batch writes and coordinate long work through
  `WorkCoordinator` plus backend operation jobs.
- `LrShutdownFunction` uses native `pcall(doneFunc)` and performs no I/O,
  logging, HTTP, task, or process work. Backend idle/recovery logic owns exit.
- Keep Chroma `image_embeddings` and `edit_training` isolated. Every vision-LLM
  item retains its own pixels; batch `/metadata/generate_batch` requests and
  keep default local-LLM concurrency at one.
- Learned styles map target-independent source evidence to absolute Develop
  targets. Do not restore genre taxonomies, keyword gates, scene-probe labels,
  or additive edit targets. Ambiguous policy membership abstains.
- Use durable per-photo jobs and atomic resource-vector admission. Maintenance
  operations require validated backups/ownership and must not race live work.
- Build and validate inactive policy generations before atomic activation.
  Preserve the prior active generation, custom names, recommendation feedback,
  and immutable edit history on failure/reset.
- Localize visible UI in English, Catalan, German, Spanish, and French. Run the
  backend suite, lint, and Lightroom plug-in validator for relevant changes.
