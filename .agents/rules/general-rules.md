---
trigger: always_on
---

# StyleAI Core Agent Guardrails

Key development constraints for StyleAI. The repository `AGENTS.md` is the
authoritative architecture specification; update this summary whenever that
architecture changes.

## 1. Error Handling & Logging
- **User Errors**: Surface backend errors/warnings in Lightroom GUI via `ErrorHandler.handleError`.
- **Logging**: Use configured `logger` in Python with `exc_info=True`. Log plugin events using `log:error`/`warn`/`info`/`trace`.

## 2. Lua Plugin Rules
- **Yielding & Async**: Run long tasks in `LrTasks.startAsyncTask`. Use `LrTasks.pcall` for normal async tasks. However, Lightroom shutdown hooks (`doneFunc`) MUST use native `pcall` because the async scheduler is unreliable during teardown and `LrTasks.pcall` will hang. NEVER use `LrTasks.execute` inside a teardown hook because it yields, and keep teardown free of HTTP, file, logging, and process-launch work; the backend owns idle shutdown.
- **Spin-Locks & Yielding**: NEVER call `LrTasks.yield()` inside `withWriteAccessDo` closures. On macOS, use `LrTasks.yield(); LrTasks.sleep(0.01)`.
- **Transactions & Collections**: Wrap batch updates in a **single** `withWriteAccessDo` block. Never call `getChildCollections()` on a newly created set inside the same transaction.
- **UI Bounds**: Avoid `share()` or `width_in_chars` on mixed UI controls. Use explicit pixel widths (e.g. `width = 600`) in centered columns.
- **Nil Checks & State**: Clear hidden UI property bindings on mode reset. Initialize empty tables. Search codebase on refactors to clean lingering references.

## 3. Python Backend Rules
- **Imports & Layering**: `routes/` (Blueprints), `services/` (logic), `providers/` (LLM). Relative imports inside subpackages, absolute across subpackages.
- **Memory Optimization**: ALWAYS call `Image.thumbnail()` BEFORE `.convert("RGB")` when processing images to prevent OOM RAM spikes, even when just generating base64 payloads for the LLM.
- **Local Boundary**: Bind production REST to loopback port `19819`. LLMs are
  locally running open-weights models through Ollama or LM Studio only. Never
  add cloud providers, API keys, remote backend URLs, or image/metadata egress.
- **Catalog Ownership**: One backend/database belongs to one Lightroom catalog.
  Do not add multi-catalog routing or shared-database identifiers.
- **Durable Workflows**: Indexing, tagging, training/discovery, recommendations,
  and editing use catalog-local operation jobs. Backend completion remains
  nonterminal while a Lightroom catalog or Develop handoff is pending.
- **Maintenance Barrier**: Restore, backup, pruning, destructive reset, and
  policy rebuild/activation must acquire maintenance admission so live
  inference-to-commit workflows drain before database replacement or mutation.

## 4. ML Architecture & Taxonomy Rules
- **DB Isolation**: Keep ChromaDB `image_embeddings` and `edit_training`
  collections strictly isolated.
- **Taxonomy-Free Discovery**: Do not restore genre buckets, semantic genre
  caches, keyword exception ladders, or fixed scene-probe labels. Discover
  editing policies from edited-target behavior and source-space
  recognizability; descriptors explain policies but never admit members.
- **Absolute Targets**: A learned policy maps unedited source evidence to
  absolute Lightroom targets. Current settings are application inputs only;
  full-strength application must be idempotent.
- **Training Pixels**: "Learn From My Edits" uses image exports for pixel
  metrics. A text-only metadata path may proceed without training pixels, but a
  vision-metadata job must retain and infer from its own photo bytes.
- **LLM Batching & Concurrency**: Batch metadata requests to `/metadata/generate_batch`. Never call `/metadata/generate` sequentially in loops. NEVER increase `STYLEAI_LLM_CONCURRENCY` above 1 by default, as running parallel local LLM requests (Ollama/LM Studio) will stall the GPU and deadlock processing.
- **GPU Worker Sync**: Pause downstream LLM workers on `active_embeddings_uuids` gate until upstream vision workers commit embeddings.
- **Recommendation Precision**: Retrieve bounded multi-medoid neighborhoods,
  then require calibrated component membership and low ambiguity before burst
  deduplication, coverage, and quality ranking. Recommendation generation is a
  cancellable background operation, never one long-held HTTP request.
