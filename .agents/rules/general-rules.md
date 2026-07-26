---
trigger: always_on
---

# StyleAI Core Agent Guardrails

Key development constraints for StyleAI. For complete architecture specifications, see [AGENTS.md](file:///Users/anna/Documents/Coding/StyleAI/AGENTS.md).

## 1. Error Handling & Logging
- **User Errors**: Surface backend errors/warnings in Lightroom GUI via `ErrorHandler.handleError`.
- **Logging**: Use configured `logger` in Python with `exc_info=True`. Log plugin events using `log:error`/`warn`/`info`/`trace`.

## 2. Lua Plugin Rules
- **Yielding & Async**: Run long tasks in `LrTasks.startAsyncTask`. Use `LrTasks.pcall` (NEVER native `pcall`, especially wrapping shutdown `doneFunc`).
- **Spin-Locks & Yielding**: NEVER call `LrTasks.yield()` inside `withWriteAccessDo` closures. On macOS, use `LrTasks.yield(); LrTasks.sleep(0.01)`.
- **Transactions & Collections**: Wrap batch updates in a **single** `withWriteAccessDo` block. Never call `getChildCollections()` on a newly created set inside the same transaction.
- **UI Bounds**: Avoid `share()` or `width_in_chars` on mixed UI controls. Use explicit pixel widths (e.g. `width = 600`) in centered columns.
- **Nil Checks & State**: Clear hidden UI property bindings on mode reset. Initialize empty tables. Search codebase on refactors to clean lingering references.

## 3. Python Backend Rules
- **Imports & Layering**: `routes/` (Blueprints), `services/` (logic), `providers/` (LLM). Relative imports inside subpackages, absolute across subpackages.
- **Memory Optimization**: ALWAYS call `Image.thumbnail()` BEFORE `.convert("RGB")` when processing images to prevent OOM RAM spikes.
- **Docker**: Sync `Dockerfile` and compose files when dependencies change.

## 4. ML Architecture & Taxonomy Rules
- **DB Isolation**: Keep ChromaDB `photos` and `training_examples` collections strictly isolated.
- **Training Pixels**: "Train AI Style" requires JPEG exports for raw pixel metrics. Missing JPEGs during text-only metadata generation must handle gracefully without HTTP 400.
- **LLM Batching**: Batch metadata requests to `/metadata/generate_batch`. Never call `/metadata/generate` sequentially in loops.
- **Classifier Versioning**: Increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py` when changing grouping rules to purge `semantic_genre_cache`. All style routes must call `catalog_service._ensure_initialized()`.
- **GPU Worker Sync**: Pause downstream LLM workers on `active_embeddings_uuids` gate until upstream vision workers commit embeddings.
