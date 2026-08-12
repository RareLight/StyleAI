# Major Workflow Performance Optimization Checklist

This checklist covers performance work for style application, photo indexing and
local metadata generation, and style-upgrade recommendations. Optimize bounded
or repeated orchestration work before changing model behavior. Every completed
item must preserve catalog-local operation semantics, cancellation, per-photo
results, source-evidence contracts, and exact Lightroom readback.

## Phase 1: exact-behavior optimizations

### Style-upgrade recommendations

- [x] Calculate each policy's current and needed example counts before querying
      Chroma.
- [x] Omit policies with `needed_count == 0` before applying the policy budget.
- [x] Apply a deterministic ordering to eligible policies so complete policies
      cannot starve undertrained policies.
- [x] Request only data actually consumed by the initial neighbor query.
- [x] Preserve bounded multi-medoid retrieval, membership-precision admission,
      ambiguity abstention, burst deduplication, and cross-policy isolation.
- [x] Test that complete policies perform no neighbor search or review write.
- [x] Test that the policy limit is applied after eligibility filtering.

### Embeddings and metadata indexing

- [x] Validate each submitted batch using the job header plus only its submitted
      operation items; never materialize the complete operation item list.
- [x] Preserve duplicate-ID, foreign-ID, terminal-job, and cancellation errors.
- [x] Publish worker and metadata groups of `running`, `committing`, `succeeded`,
      `failed`, and `canceled` states atomically where they share a job.
- [ ] Batch `queued` admission only after an orphan-safe queue/ledger protocol is
      designed; the current pre-enqueue publication remains intentionally isolated.
- [x] Preserve per-photo result/error payloads and cancellation coercion.
- [x] Extract rendered JPEG location metadata only for photos that actually need
      local metadata generation; skip it for embeddings-only work.
- [x] Keep metadata inference tied to each photo's own rendered pixels.
- [x] Test embeddings-only, metadata-only, combined, no-op, partial failure,
      foreign item, and cancellation paths.

### Style application

- [x] Fetch canonical source records for a bounded edit batch in one Chroma read.
- [x] Reconstruct records by photo ID rather than relying on Chroma result order.
- [x] Preserve complete source-stamp compatibility checks and recompute every
      incompatible or incomplete record.
- [x] Batch operation-item state publication without changing inference,
      persistence, Lightroom handoff, or per-photo failures.
- [x] Test mixed cache hits/misses, reordered Chroma results, missing metrics,
      incompatible stamps, cancellation, and partial inference failure.

## Phase 2: measured storage and orchestration batching

### Shared instrumentation

- [x] Record bounded batch timings for preparation, model inference, storage,
      operation-ledger publication, and total work.
- [x] Report counts and byte/queue peaks rather than logging sensitive inputs.
- [x] Confirm instrumentation is bounded and does not add per-photo INFO noise.

### Index storage

- [x] Replace redundant record re-reads with the batch's existing-record map.
- [ ] Prototype bounded Chroma upserts while retaining exact metadata and source
      stamps.
- [ ] Fall back to isolated writes when a bulk storage operation fails.
- [ ] Do not release combined metadata work until that photo's embedding commit
      is durable.
- [ ] Benchmark 120-, 1,000-, and 10,000-item operation shapes.

### Style-edit persistence and Lightroom orchestration

- [ ] Measure Lightroom export, write-access, exact-readback, inference, and
      receipt-publication time independently.
- [ ] Batch backend inference-history and operation-state persistence in bounded
      chunks with per-item fallback.
- [ ] Preserve immutable inference IDs and crash-safe application receipts.
- [ ] Design a chunked Lightroom exporter using copied settings, an owned
      temporary directory, the render lane, per-rendition results, and cleanup.
- [ ] Keep isolated application for review mode, profile/HDR changes, virtual
      copies, rollback, and any failed chunk.

### Recommendation candidate reuse

- [ ] Reuse normalized candidate embeddings and basic candidate attributes
      across compatible policy artifacts.
- [ ] Evaluate consolidating compatible anchor queries and de-duplicating the
      subsequent candidate fetch.
- [ ] Bound all candidate IDs, embeddings, and working matrices.
- [ ] Re-profile before adding persistent result caching.

## Phase 3: higher-risk experiments

- [ ] Fuse embedding decode and exposure-metric preparation only if numerical
      evidence is identical; otherwise version the evidence contract.
- [ ] Evaluate fewer Lightroom write transactions only after crash, rollback,
      exact-readback, review, virtual-copy, profile, and HDR tests pass.
- [ ] Consider recommendation caching only with a durable revision that covers
      embeddings, relevant metadata, ratings, picks, training, and generations.
- [ ] Do not increase local-LLM concurrency, skip independent burst predictions,
      copy metadata between photos, or weaken exact Lightroom readback.

## Required validation before release

- [x] Run focused route, operation, indexing, style-edit, recommendation, and
      policy-admission tests.
- [x] Run the complete backend pytest suite.
- [x] Run backend lint/format checks.
- [x] Run `python scripts/validate_lrc_plugin.py`.
- [ ] Run translation synchronization validation when visible strings change.
- [x] Compare model outputs and stored source stamps before and after changes
      through compatibility and policy regression fixtures.
- [ ] Exercise Lightroom cancellation, retry, backend restart, idle recovery,
      combined indexing, style application, and recommendation discovery.
- [x] Record any required Lightroom rerun if source evidence, grouping, EXIF,
      schema, or learned artifacts change.
