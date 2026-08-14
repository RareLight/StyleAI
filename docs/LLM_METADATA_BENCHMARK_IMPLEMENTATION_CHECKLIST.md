# LLM Metadata Benchmark Implementation Checklist

This checklist defines the developer-only benchmark used to compare local
multimodal LLMs and quantization variants against one frozen Lightroom photo
set. The benchmark measures performance and preserves exact normalized metadata
outputs without writing generated metadata to Lightroom, Chroma, or learned
editing state.

## Product boundary and acceptance criteria

- [x] Register the workflow in the canonical plug-in under Lightroom's
      **Help > Plug-in Extras** menu, separate from normal File-menu workflows.
- [x] Snapshot the selected photos before opening any modal dialog and reject an
      empty selection.
- [x] Create one uniquely named regular collection under `StyleAI → Benchmarks`
      for the frozen benchmark set without changing Lightroom's active source.
- [x] Detect locally available vision-capable Ollama and LM Studio models and
      allow selecting one or more provider/model pairs.
- [x] Hold the prompt, output fields, language, temperature, keyword hierarchy,
      and optional context controls constant across selected models.
- [x] Prepare each Lightroom-rendered JPEG proxy once and reuse the exact bytes
      for every model. Record a proxy fingerprint and byte count.
- [x] Never write benchmark titles, captions, alt text, or keywords to the
      Lightroom catalog, `image_embeddings`, `edit_training`, styles SQLite
      tables, or learned model artifacts.
- [x] Keep all provider calls loopback/local and retain the standard response
      envelope and operation-scoped cancellation behavior.
- [x] Persist partial output incrementally and reveal the report folder in
      Finder or Explorer after success, cancellation, or partial failure.

## Reproducibility and benchmark protocol

- [x] Assign a report schema version and a unique run ID.
- [x] Record plug-in/backend versions, platform, machine summary, selected model
      names, exact system prompt, normalized output contract, generation
      settings, and context flags.
- [ ] Add immutable provider-reported model digests when Ollama and LM Studio
      expose a consistent local identity contract; exact model names are
      recorded today.
- [x] Preserve a deterministic photo order and record stable photo IDs and
      display filenames without absolute source paths.
- [x] Record the exact proxy fingerprint, dimensions, and byte size used for
      each photo so input equality can be audited.
- [x] Perform one excluded warm-up request per model and report cold/warm timing
      separately. Make warm-up behavior explicit in the manifest.
- [ ] When repeat trials are implemented, rotate or deterministically randomize
      model order and record the order and seed. The current single-trial
      workflow records its fixed order.
- [x] Keep default LLM concurrency at one. Record effective concurrency and do
      not mix concurrency tuning into model-quality comparisons.
- [x] Record retries and failures explicitly rather than silently blending them
      into successful inference timing.

## Backend service and HTTP boundary

- [x] Add a dedicated benchmark service that reuses normal provider prompt,
      schema, response parsing, and normalization behavior but performs no
      indexing or database writes.
- [x] Add a bounded `POST /metadata_benchmark/run_batch` endpoint accepting at
      most 12 inline JPEG items and one provider/model configuration.
- [x] Validate request shape, unique photo IDs, image byte budget, provider,
      model, output selection, and benchmark operation ownership.
- [x] Add `metadata_benchmark` to the operation registry and represent each
      model/photo pair with a composite operation item ID.
- [x] Acquire accelerator, LLM, CPU-preparation, and image-byte resources
      atomically through the existing admission controller.
- [x] Return one result per photo containing status, normalized keywords, title,
      caption, alt text, warning/error, retry count, token usage, and timing.
- [x] Measure admission wait, decode/preparation, provider request, and total
      server duration. Preserve provider-specific upload/model-load/inference,
      first-token, and throughput metrics when the SDK exposes them.
- [x] Ensure cancellation marks unfinished operation items and releases all
      resources without affecting unrelated jobs.
- [x] Add route, service, privacy, cancellation, limit, malformed-input, and
      no-persistence tests.

## Lightroom orchestration

- [x] Add `TaskMetadataBenchmark.lua` as a thin async entry point and keep
      workflow logic in a reusable `MetadataBenchmark.lua` module.
- [x] Reuse `PhotoSelector.snapshotSelectedPhotos`, standardized photo IDs,
      EXIF/context helpers, and `WorkCoordinator` export/request/catalog-write
      lanes.
- [x] Provide a resizable configuration dialog with a multi-select model list,
      benchmark-set summary, generated-field switches, prompt, language,
      temperature, context switches, and warm-up option. The catalog-local
      report destination is fixed and revealed after the run.
- [x] Default all generated fields on and use the shipped metadata prompt
      without changing the user's normal Prepare Photos preferences.
- [x] Create and populate the benchmark collection in a single bounded catalog
      write transaction; do not query a collection set created in that same
      transaction.
- [x] Prepare bounded proxies before model execution, yielding outside catalog
      transactions and cleaning temporary exports after the final model.
- [x] Start one durable operation containing all model/photo pair item IDs and
      use its ID for status, cancellation, and recovery.
- [x] Run selected models successively, batch photos within the endpoint limit,
      update progress by model/photo pair, and continue past isolated failures.
- [x] On cancellation, stop submitting new batches, request scoped backend
      cancellation, finalize the partial report, and leave the frozen Lightroom
      collection intact.

## Reports and qualitative comparison

- [x] Create a unique report directory beneath the catalog-local
      `styleai.db/evaluation_reports` directory.
- [x] Write `manifest.json` atomically and update its run state as the benchmark
      progresses.
- [x] Append one self-contained record per model/photo result to
      `results.jsonl`, flushing after every record or completed batch.
- [x] Generate `summary.csv` with per-model success/failure counts, total time,
      cold-start time, steady-state mean/median/p90/p95 latency, photos/minute,
      token totals, and median tokens/second where available.
- [x] Generate `comparison.csv` with one model/photo row and complete normalized
      metadata fields suitable for spreadsheet analysis.
- [x] Generate a self-contained escaped `report.html` that groups outputs by
      photo and compares models side by side without embedding source pixels by
      default.
- [x] Clearly label partial/canceled runs, failed images, retries, unavailable
      timing fields, and excluded warm-up samples.
- [x] Reserve a schema-compatible optional blinded-review section for later
      ratings of correctness, specificity, search usefulness, alt-text quality,
      and hallucination severity.
- [x] Exclude absolute source paths, image bytes, existing private metadata,
      prompts supplied for individual photos, and unbounded provider logs from
      reports unless a future explicit diagnostic opt-in is added.

## Developer packaging, localization, and documentation

- [x] Add a literal developer Help-menu registration to the canonical manifest.
- [x] Keep the existing embedding throughput benchmark as a separate command
      with an unambiguous title.
- [x] Add every visible string to English, Catalan, German, Spanish, and French
      resources and run translation synchronization.
- [x] Update UI behavior contracts and the developer guide with the benchmark's
      non-persistence, privacy, report, and cancellation contracts.
- [x] Add packaging tests proving every compatibility package contains the same
      developer Help commands.

## Automated validation

- [x] Run focused backend service/route tests.
- [x] Run Lua workflow and package contract tests.
- [x] Run the repository's Ruff lint and format checks (`ruff check src test` and
      `ruff format --check src test`, the checks in `lint_format.sh`).
- [x] Run the complete server test suite in the existing Python 3.12 environment
      (`536 passed`; equivalent test target to `uv run pytest test/`).
- [x] Run `python scripts/validate_lrc_plugin.py`.
- [x] Run `python sync_translations.py` and confirm no unsynchronized resources.
- [x] Build both disposable packages and inspect their manifests.

## Required human validation

- [ ] Select a compatible LM Studio draft for one vision model, leave paired
      baseline comparison enabled, and verify baseline and speculative rows use
      identical proxies while reporting separate timings and draft-token
      acceptance statistics.
- [ ] Confirm an incompatible draft fails during its excluded warm-up and its
      measured photos are marked skipped without repeated inference attempts.
- [ ] Confirm LM Studio saved speculative defaults are disabled and the report
      does not identify a draft model as used for a baseline row.
- [ ] From **Help > Plug-in Extras**, choose a mixed 24–32 photo set and verify the
      uniquely named collection contains exactly the frozen selection without
      becoming Lightroom's active source.
- [ ] Run at least two vision-capable local model variants and confirm identical
      proxies/photo order, successive model execution, responsive cancellation,
      and no Lightroom or Chroma metadata mutation.
- [ ] Inspect Finder/Explorer reveal behavior and JSONL, CSV, and HTML reports
      after complete, partial-failure, and canceled runs.
- [ ] Compare backend/provider timing with LM Studio or Ollama diagnostics and
      confirm cold-start versus steady-state labeling is credible.
- [ ] Verify dialog layout and localized strings on macOS and Windows scaling.
