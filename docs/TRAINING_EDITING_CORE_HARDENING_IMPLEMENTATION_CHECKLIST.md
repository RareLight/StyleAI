# Training and Editing Core Hardening Implementation Checklist

Status: core implementation and automated validation complete; deferred
optimizations, catalog-baseline comparison, and Lightroom human gates remain.
This checklist is the source of truth for the end-to-end training and Apply My
Style hardening work identified by the August 2026 architecture review.

## Product and validation boundaries

- [x] Preserve local-only operation and the catalog-local database boundary.
- [x] Preserve absolute-target, idempotent strength semantics.
- [x] Preserve burst-safe validation, ambiguity abstention, rendering-state
  readback, immutable inference history, and atomic generation activation.
- [x] Keep every behavior change backward-compatible at the HTTP boundary
  unless a versioned schema change is explicitly required.
- [x] Add focused regression tests before or with each behavior change.
- [x] Update user/developer documentation and all five Lightroom translations
  for visible behavior changes.
- [x] Record the exact Lightroom rerun required by each data/model change.

## 1. Normalize multi-output targets during model fitting

- [x] Add weighted robust target location and scale to the shared estimator
  standardizer, with finite nonzero fallbacks for constant targets.
- [x] Fit reduced-rank ridge, weighted PLS, multi-task Elastic Net, and the
  bounded nonlinear challenger in normalized target space.
- [x] Invert target scaling exactly during prediction and preserve artifact
  serialization compatibility for newly rebuilt generations.
- [x] Preserve the robust target-median baseline in Lightroom target units.
- [x] Add unit-invariance tests proving that multiplying one target by a large
  constant does not change normalized per-target accuracy.
- [x] Add constant-target, weighted-outlier, serialization, and candidate
  benchmark regression tests.
- [x] Bump the learned-policy algorithm version so old artifacts cannot be
  confused with normalized-target artifacts.
- [x] Document that existing catalogs must run **Styles & Training → Rebuild**.

## 2. Enforce genuinely target-independent source evidence

- [x] Centralize training source resolution through the canonical neutral-source
  service rather than duplicating RAW-preview extraction in the route.
- [x] Reject or explicitly exclude `lightroom_rendered_preview` evidence from
  learned policy fitting; never admit edited pixels as training features.
- [x] Do not store dummy embeddings as successful trainable examples.
- [x] Return a per-photo exclusion/error reason when a neutral source embedding
  cannot be produced.
- [x] Ensure readiness counts only neutral, stamped, finite embeddings with a
  usable target and non-panorama geometry.
- [x] Add RAW-preview success, rendered-fallback exclusion, decode failure,
  panorama, and stale/missing stamp tests.
- [x] Document that affected photos must run **Learn From My Edits** again; run
  **Prepare Photos** first only when canonical indexed evidence is missing or
  stale.

## 3. Separate source-photo identity from virtual-copy application identity

- [x] Persist generated recipe provenance without making an untouched source
  photo eligible for applied-edit outcome review.
- [x] Attach inference/application tracking metadata only to the Lightroom photo
  instance that actually received the edit.
- [x] Store catalog-local source-to-copy lineage in the application receipt or
  immutable application event without changing global photo identity.
- [x] Make recipe persistence return explicit success/error values; callers must
  not treat a logged internal failure as success.
- [x] Ensure virtual copies and originals cannot submit conflicting outcomes for
  the same inference.
- [ ] Add Lua validation fixtures/static checks for original-edit, virtual-copy,
  cancel, persistence failure, and outcome-selection behavior.
- [x] Keep virtual copies default-on on first launch, remember later selection,
  add accepted copies to the concise unique collection, and never focus it.

## 4. Eliminate quadratic operation-item lookup

- [x] Add indexed `get_job_item` and bounded `get_job_items` service APIs.
- [x] Fetch job headers with `include_items=false` at request boundaries.
- [x] Validate only the item IDs present in the current training/edit batch.
- [ ] Add a bulk item-state transition where it reduces connection churn while
  preserving per-item error and cancellation semantics.
- [x] Add query-shape/count tests demonstrating linear total work across many
  batches and preserving idempotent retry behavior.

## 5. Avoid unnecessary training extraction and inference

- [x] Add a preflight endpoint/service contract for existing IDs, operation
  state, file eligibility, and force-retrain behavior.
- [x] Run preflight before Lightroom creates/encodes JPEG previews.
- [x] Reuse a fully compatible canonical source embedding before recomputing.
- [x] Batch remaining neutral-source decode and SigLIP2 inference according to
  the pressure-aware GPU batch recommendation.
- [ ] Batch Chroma upserts without unbounded in-memory accumulation.
- [x] Remove histogram-signature, histogram-distance, and dominant-color
  training code, metadata fields, tests, and documentation references.
- [x] Retain only source exposure metrics that are consumed by production
  features, burst gates, or evaluation.

## 6. Publish one authoritative readiness contract

- [x] Replace raw-count thresholds with eligible examples per normalized
  profile/HDR partition and the production minimum partition size.
- [x] Return active generation ID/status, eligible partition counts, exclusions
  by reason, and a localized next action.
- [x] Remove the hard-coded Lightroom 5-example gate and legacy 10-example
  `has_enough_examples` interpretation.
- [x] Keep compatibility fields only where necessary and derive them from the
  authoritative contract.
- [x] Add cold-start, excluded-only, split-partition, ready-but-not-built, and
  active-generation tests.

## 7. Harden per-item batch fallback and extraction edge cases

- [x] Treat an individual prepared source with no embedding as unavailable and
  retry through the ordinary per-photo path.
- [x] Add mixed-success batch tests where exactly one source decode/model call
  fails.
- [x] Replace truthiness-based Color Grading fallback selection with explicit
  `None` handling so valid zero values survive.
- [x] Add zero hue, saturation, luminance, balance, and blending tests.

## 8. Pin each Apply My Style operation to one learned generation

- [x] Persist the selected generation ID and schema versions in operation job
  details on the first edit request.
- [x] Require every later batch/retry in that operation to load the pinned
  generation and reject incompatible changes safely.
- [x] Retain pinned retired artifacts while a nonterminal job references them.
- [x] Release/prune artifacts only after all referencing jobs are terminal.
- [ ] Add concurrent activation, lost-response retry, cancellation, recovery,
  and prune tests.

## 9. Make burst coherence operation-wide and computationally useful

- [x] Add a lightweight EXIF/evidence prepass so temporal neighbors are packed
  together independent of Lightroom view order.
- [ ] Preserve bounded memory with deterministic windows and boundary overlap;
  do not build a catalog-wide similarity matrix.
- [ ] Carry boundary state across request batches so bursts longer than the HTTP
  batch size remain one operation-scoped group where safe.
- [ ] Vectorize independent policy inference by hard partition while preserving
  each member's own source features, selectors, calibrators, local correction,
  inference record, and Lightroom review.
- [x] Keep exact target reuse off by default until held-out and Lightroom human
  gates in the burst-coherence checklist pass.
- [x] Report actual avoided model work rather than counting classification alone.
  Current production diagnostics correctly report zero avoided policy
  predictions; source embedding inference is the batched savings today.

## 10. Bound and cancel model rebuild work

- [x] Traverse Chroma examples in bounded pages and avoid duplicate full-catalog
  Python representations where possible.
- [x] Store normalized embedding arrays as float32 unless a measured numerical
  requirement needs float64 for a specific fit.
- [ ] Add cancellation checks between pages, partitions, discovery iterations,
  cross-validation folds, candidate fits, persistence, and activation.
- [x] Keep the prior active generation untouched after cancellation or failure.
- [ ] Extend pressure-aware limits to rebuild staging and final-fit memory.
- [ ] Add large synthetic catalog, cancellation, memory-bound, and recovery tests.

## 11. Remove dead or misleading Apply My Style controls

- [x] Remove **Apply Masks** from the Apply My Style options and per-photo review
  dialog.
- [x] Remove its preference, request option, recipe-application branch, visible
  strings, and outdated UI contract/checklist claims for this workflow.
- [x] Keep unrelated mask infrastructure intact if it is used by another
  product workflow; do not imply the learned policy supports masks.
- [x] Gate Profile/HDR Auto with the review dialog's global-edit selection, or
  expose a separate explicit rendering choice if future product requirements
  demand it.
- [x] Add Lightroom static validation for the simplified option/review state.

## 12. Documentation, evaluation, and release gates

- [x] Update Architecture, Plugin Guide, Developer Guide, troubleshooting, UI
  behavior contracts, and the burst-coherence checklist where behavior changed.
- [x] Synchronize English, Catalan, German, Spanish, and French resources.
- [x] Run focused policy model, training route/service, operation, style-edit,
  rendering, history, and recovery tests.
- [ ] Run `bash server/scripts/lint_format.sh`.
- [x] Run `(cd server && uv run pytest test/)` or the repository-local equivalent.
- [x] Run `python scripts/validate_lrc_plugin.py`.
- [x] Run `python sync_translations.py` and verify no unintended resource drift.
- [ ] Run policy evaluation scripts and compare normalized target error,
  selective coverage, membership precision, ambiguity abstention, and
  cross-policy leakage against the pre-change baseline.
- [ ] Perform Lightroom human checks for first-launch defaults, remembered
  preferences, virtual-copy lineage, collection placement/focus, cancellation,
  per-photo review, profile/HDR gating, and completion messaging.

Validation note (2026-08-09): the repository lint wrapper could not initialize
the sandbox-inaccessible user uv cache. The installed Ruff executable was run
directly instead; `ruff check src test` and `ruff format --check src test` both
passed. The complete repository-local pytest run passed 458 tests. Synthetic
policy evaluation and the 600/2,048/10,000-example scaling benchmark completed;
a pre-change catalog baseline was not available for a before/after comparison.

## Lightroom rerun matrix

- Target normalization or applicability-model changes:
  **Styles & Training → Rebuild**.
- Neutral-source admission or source-stamp changes:
  **Learn From My Edits** again for affected photos; use **Prepare Photos** first
  when canonical indexed evidence is missing or stale.
- Operation lookup, UI, virtual-copy lineage, readiness presentation, and burst
  batching changes: no training rerun by themselves.
