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

## Planned MTP-aware speculative benchmarking

### Discovery and capability model

- [x] Replace the single undifferentiated draft-candidate list with explicit
      `full_draft`, `mtp_integrated`, and `mtp_sidecar` kinds plus an explicit
      `unknown` capability state. Treat filename matching only as a conservative
      classification hint, never as proof of compatibility.
- [x] Preserve LM Studio's stable model key, runtime/engine, distribution
      format, architecture, vocabulary/tokenizer identity, repository,
      revision, quantization, and any native speculative-decoding capability
      fields returned by the installed LM Studio SDK or native API.
- [x] Prefer a live LM Studio compatibility/capability result over StyleAI's
      heuristics. Do not maintain a hard-coded model-family allowlist as the
      source of truth.
- [x] Represent capability as `supported`, `unsupported`, or `unknown`, with a
      short machine-readable reason and user-facing explanation. Fail closed
      when a pairing is known to be invalid; permit an explicit warm-up probe
      only when capability is unknown.
- [x] Never present a standalone `mtp-*` sidecar as an ordinary full drafting
      model. Recognize an integrated MTP GGUF as one target model with embedded
      NextN/MTP tensors rather than as a target-plus-draft pairing.
- [x] Keep MLX and GGUF candidates separate. Reject cross-runtime and
      cross-format pairings before a benchmark starts.

### Benchmark user interface

- [x] Keep explicit **Full draft model** selection separate from automatic
      integrated MTP. An integrated-MTP GGUF runs as one target configuration;
      it is not a selectable draft mode and receives no prediction override.
- [x] Do not offer a synthetic MTP-off comparison unless LM Studio exposes a
      reliable disable control. Never ask the user to select a separate MTP file
      for a self-contained integrated GGUF.
- [x] Keep sidecar MTP artifacts unavailable unless LM Studio reports that the
      exact target/runtime can load them through its MTP path. The current SDK
      exposes no such pairing, so report their hidden count and concise reason
      instead of silently omitting or enabling them.
- [x] Include format, quantization, publisher/repository, and architecture in
      pairing labels while keeping the image counter visible in progress text.
- [x] Apply **Also run a baseline** only to explicit full-draft comparisons.
      Do not label an automatically accelerated integrated target as baseline.
- [x] Add preflight errors for cross-format pairs, target=self draft selection,
      MTP sidecars selected as full drafts, incompatible vocabulary, and absent
      draft-token activity for explicit full drafts.

### Runtime execution and verification

- [x] Add a provider preflight operation that loads or inspects the exact model
      configuration without processing the benchmark photo set, then reports
      the effective speculation mode and compatibility evidence.
- [x] For integrated MTP, issue ordinary inference and let LM Studio activate
      the embedded head automatically at model load. Do not require or mutate a
      saved speculative setting.
- [x] Remove the current behavior that first submits an MTP sidecar through the
      prediction-time `draftModel` setting merely to discover a capability-gap
      error.
- [x] Treat missing draft-token activity as an error only for an explicitly
      selected full draft. For automatic integrated MTP, record
      `runtime_managed_unreported`, retain the successful metadata result, and
      leave effective mode/request activity `unknown` rather than claiming MTP.
- [x] Record requested mode, effective mode, target identity, draft/sidecar
      identity when applicable, runtime version, load parameters, draft depth,
      proposed/accepted/rejected token counts, acceptance rate, and fallback
      reason in the manifest and per-result records.
- [x] Keep baseline and speculative image bytes, prompt, sampling parameters,
      context, output schema, and model quantization identical. Record SDK model
      lookup/load time separately; user-performed unload/reload happens before
      the run and is explicitly outside benchmark inference timing.
- [x] Begin MTP validation with conservative runtime defaults. Benchmark draft
      depth and probability thresholds only as separately labelled performance
      experiments, not as hidden defaults.

### Tests and documentation

- [x] Add discovery fixtures for an ordinary full draft, integrated MTP GGUF,
      matching MTP sidecar, misleading `mtp` name, MLX model, and incompatible
      cross-format pair.
- [x] Add provider tests for capability-supported, unsupported, unknown,
      load-time-only, no-draft-activity, and speculative-batch-failure results.
- [x] Add Lua UI contract tests proving MTP artifacts cannot appear in the full
      draft selector and incompatible choices cannot be submitted.
- [x] Add report-schema tests for the effective speculation mode and runtime
      evidence while preserving old non-speculative report readability.
- [x] Classify vision-sidecar load failures, rejected integrated-MTP tensors,
      speculative-batch failures, and generic runtime load failures in CSV and
      JSONL output.
- [x] Document the distinction between full-model drafting, integrated MTP, and
      MTP sidecars in the developer guide, including LM Studio/runtime version
      dependence and the text-first then vision warm-up procedure.
- [ ] Validate with a runtime-confirmed Qwen 3.5/3.6 MTP model, a Gemma 4 model
      only after its installed LM Studio runtime advertises compatibility, and
      one deliberately unsupported MTP artifact.

## August 2026 external benchmark-review backlog

The external review was prepared without repository access. The items below
reconcile its useful recommendations with the implementation above; they are a
backlog, not instructions to bypass StyleAI's local-only/provider boundaries.

### Completed or already covered

- [x] Keep requested speculation, effective speculation, request-level vision
      presence, and request-level verified/unknown speculation activity distinct.
      Automatic MTP without telemetry is successful but never reported as active.
- [x] Persist requested/used draft identity, configuration, load context,
      accepted/rejected/ignored draft counters, acceptance rate, SDK version,
      verification status, fallback reason, and classified failures where LM
      Studio exposes them.
- [x] Preserve identical prepared proxy bytes per photo/model within a run and
      record hashes, dimensions, byte counts, deterministic order, prompt,
      output contract, sampling overrides, concurrency, and warm-up policy.
- [x] Report photo-level throughput with explicit units: photos/minute,
      images/second, seconds/image, photos/hour, and projected hours for 1,000
      and 10,000 photos. Keep tokens/second labelled as backend-specific.
- [x] Report mean, median, p90, p95, standard deviation, coefficient of
      variation, completion rate, warm-up timing, and total item timing.
- [x] Add per-result and aggregate automated contract metrics for structured
      response success, requested-field presence, the 12-keyword limit, exact
      normalized duplicates, forbidden placeholders, caption/alt-text word
      counts, and excessive lexical overlap. Keep these separate from human
      visual-quality judgments.
- [x] Record proxy mismatches instead of silently treating changed inputs as
      comparable.

### Next coherent implementation slices

- [ ] Add a durable, privacy-preserving `reviews.jsonl` (or equivalent) plus a
      report review UI for per-photo/model correctness, specificity, search
      usefulness, alt-text quality, hallucination severity, optional preference,
      and notes. Preserve blinded labels and never mix human scores with contract
      metrics. The current manifest only reserves this schema; no score-writing
      path exists.
- [ ] Add repeat-trial definitions, deterministic alternating/randomized model
      order with a recorded seed, and warnings for small samples, reloads,
      incomplete warm-up, background concurrency, and possible thermal drift.
- [ ] Add a cross-report comparator that admits rows only when model artifact,
      prompt/settings/output contract, source photo ID, proxy hash, and proxy
      dimensions match. Report exact match, normalized keyword-set overlap,
      lexical field stability, output-length variation, and latency variation as
      consistency—not semantic correctness.
- [ ] Add paired baseline-versus-full-draft deltas only for configurations with
      the same base artifact and comparable inputs/settings. Do not synthesize an
      MTP-off trial for integrated GGUFs until LM Studio exposes a reliable disable
      control, and do not claim speedup without a valid paired baseline.
- [ ] Preserve more provider-supplied identity fields when reliable: exact local
      artifact/revision, dense versus MoE, total/active parameters, projector or
      vision-sidecar identity, backend/runtime version, flash-attention state,
      package size, and expert settings. Mark unavailable fields unknown; do not
      infer them from marketing names.
- [ ] Add an explainable Pareto comparison using completion, photo throughput,
      latency distribution, human quality, consistency, contract compliance, and
      model size. Do not collapse these into an opaque universal score.
- [ ] Add optional user-specified library-size projections without changing the
      fixed benchmark workload.

### Explicitly deferred or constrained

- [ ] Record additional phase timings only when Ollama or LM Studio supplies
      reliable measurements. Never manufacture vision-prefill, queue, upload,
      prefill, or decode phases by subtraction without an `estimated` label.
- [ ] Treat the suggested model matrix as a manual experiment proposal. StyleAI
      will not download models, alter LM Studio, or start long benchmarks without
      explicit user action.

## Required human validation

- [ ] Select a compatible LM Studio draft for one vision model, leave paired
      baseline comparison enabled, and verify baseline and speculative rows use
      identical proxies while reporting separate timings and draft-token
      acceptance statistics.
- [ ] Confirm an incompatible draft fails during its excluded warm-up and its
      measured photos are marked skipped without repeated inference attempts.
- [ ] Confirm LM Studio saved speculative defaults are disabled and the report
      does not identify a draft model as used for a baseline row.
- [ ] Select a self-contained integrated-MTP GGUF and confirm the report records
      `automatic_model_load` without sending a draft override or requiring draft
      counters. Confirm no misleading paired baseline is scheduled.

## Optional direct MLX fallback

- [x] Assess the public `mlx-vlm` Python API for local model loading, images,
      prompt templates, generation, and speculative drafters.
- [x] Reject imports from LM Studio's private versioned backend directories;
      they are not a stable StyleAI dependency surface.
- [ ] Add `mlx-vlm` as a macOS/Apple-Silicon-only managed dependency after the
      release packaging and lockfile are restored and validated.
- [ ] Implement it as an explicitly selected local provider in a terminable
      worker process with one-model caching, bounded local paths, cancellation,
      memory cleanup, structured-output normalization, and the existing global
      accelerator/local-LLM admission claim.
- [ ] Add compatibility probes and end-to-end tests before offering an explicit
      “Retry with MLX-VLM” action. Never fail over automatically after LM Studio
      has partially loaded a model, because that can double accelerator memory.
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
