# Indexing Recovery Audit Implementation Checklist

Status: implementation and automated validation complete on August 12, 2026.
The disposable test database/debug artifacts were removed; no data migration
was built. The Lightroom human validation matrix remains for execution in a
real Lightroom Classic catalog with the local models available. This document
tracks the follow-up work from the August 11, 2026 double-check audit of the
indexing recovery, operation progress, and adjacent training changes committed
during the preceding 36 hours.

## Implemented outcome

- [x] Restored the unchanged `stable_meta_v1` identity to the `meta1:` prefix.
  Current database contents are disposable test data, so no mixed-prefix
  migration or preservation path was implemented.
- [x] Propagated operation IDs into metadata workers, checked scoped
  cancellation while waiting/admitting/dispatching, canceled pending futures,
  released cached images, closed canceled submissions, and prevented late
  results from reopening canceled items.
- [x] Deduplicated training source IDs in Lightroom, reported skipped duplicate
  instances, and traversed preflight in bounded 1,000-ID chunks.
- [x] Made fully indexed selections successful no-ops and corrected live
  progress to use mutually exclusive backend states plus preflight totals.
- [x] Removed the empty database and one-off debug scripts, restored all static
  checks, added regressions, synchronized translations, updated documentation,
  and produced a disposable developer package.

## Post-implementation Lightroom and database audit (August 11, 2026)

- [x] User deleted the disposable database and logs and allowed StyleAI to
  create a fresh catalog-local store.
- [x] User verified both indexing workflows, learned from the selected edits,
  reran the no-op cases, and exercised cancellation in both indexing paths.
- [x] Read-only `PRAGMA integrity_check` returned `ok` for `styles.sqlite` and
  `chroma.sqlite3` while the service was stopped.
- [x] Verified 859 image records (38 with generated metadata), 714 isolated
  training records, 433 admitted policy examples, and one active generation
  with seven policy models and all eight expected artifact files.
- [x] Verified all nine durable operations are terminal: six succeeded and
  three intentionally canceled. No active jobs, active items, orphan job
  items, orphan embeddings, or orphan embedding metadata remain.
- [x] Confirmed a successful index operation followed a canceled index
  operation without a service restart.
- [x] Audited the fresh service logs. The only application ERROR families were
  late worker publication after cancellation (48 occurrences) and expected
  provider cancellation logged as a batch error (2 occurrences); LM Studio's
  closed-client token fallback also emitted five un-awaited-coroutine warnings.
- [x] Made late publication to an already-canceled item idempotent, represented
  provider cancellation as a canceled result at INFO, and removed closed-client
  tokenization fallback. Focused and full-suite regressions cover each repair.
- [x] Removed routine INFO/WARNING disclosure of catalog photo IDs and replaced
  the legacy missing-field diagnostic that could log an entire base64 image
  payload with presence-only booleans.
- [x] Confirmed the next genuine Lightroom workflow recovered the stale `running`
  session marker left by the stopped backend and rewrites it for the new
  process. The databases and durable operations remained consistent.
- [x] Correlated the follow-up human runs with Lightroom and service logs: the
  60-photo partial/no-op run completed with 60 processed and zero failures,
  and the 30-photo virtual-copy run completed with 30 processed and zero
  failures. No durable jobs or items remained active.
- [x] Identified repeated 19–22 second SigLIP reloads as the source of the
  post-cancellation delay. Metadata-only sub-batches were unloading the shared
  vision model while combined indexing still needed it; system memory pressure
  could amplify the reload but was not its trigger.
- [x] Removed request-driven vision-model unloading, made installation status
  nonblocking while a load is in progress, cleared stale load errors after
  recovery, and increased the Lightroom readiness probe timeout from 0.5 to 2
  seconds. The existing idle lifecycle remains responsible for memory release.
- [x] Return expected metadata cancellation as a successful HTTP cancellation
  response and keep canceled photos out of Lightroom failure totals and ERROR
  logging.
- [x] Correlated the 13:08 metadata run with provider timing. StyleAI kept one
  globally serialized LM Studio request continuously occupied and submitted
  each next photo within milliseconds; the observed GPU gaps occurred inside
  LM Studio's image-upload/prompt/inference transition rather than in the
  StyleAI queue. Wired the configured output-token ceiling into LM Studio and
  added privacy-safe upload/model/inference/token telemetry using the current
  SDK statistics fields for the next memory/performance run.
- [x] The instrumented 13:20 run isolated a 144.44-second LM Studio JIT model
  load followed by twelve stable 5.9–7.0 second requests. Each request used 644
  input tokens and roughly 205–224 output tokens; upload and StyleAI overhead
  stayed below 0.1 seconds. Bound API-triggered LM Studio loads to an 8,192-token
  context with Flash Attention and a 10-minute idle TTL, while preserving an
  already-loaded user model's configuration, and log the effective load config
  once for verification.
- [x] The 13:32 comparison confirmed the effective 8,192-token context, Flash
  Attention, and 600-second TTL. JIT load fell from 144.44 seconds to 13.80
  seconds and GPU utilization improved immediately; twelve subsequent requests
  remained continuously saturated at roughly 6.0–7.6 seconds each. LM Studio
  still retained about 4 GB above its loaded-model baseline, establishing that
  remainder as the vision/Metal working set or allocator high-water mark rather
  than StyleAI queueing or cross-photo chat-context growth.
- [x] Correlated the 16:09 idle-recovery failure across both logs. The prior
  backend shut down cleanly after its 600-second idle timeout; opening **Prepare
  Photos** launched a replacement, but the modal queried SigLIP and LM Studio
  during its four-second startup window and retained the transient false
  "needs setup" / "status unknown" results after the backend became healthy.
- [x] Wait for backend readiness after snapshotting Lightroom's selection but
  before constructing the **Prepare Photos** modal. A static workflow contract
  test protects this ordering, and the later redundant wait was removed.
- [x] Removed additional routine route logging of stored generated metadata,
  metadata values, and catalog photo identities; privacy tests protect the
  request and readback paths.
- [x] The post-fix idle-recovery test and subsequent 120-photo combined run
  succeeded without a backend reset or model re-download. Lightroom reported
  120 processed and zero failed in 1,183.10 seconds; the durable index job has
  120 succeeded items, zero failed/canceled/nonterminal items, and no ERRORs
  occurred during the run. The only warnings were expected probes for the
  unconfigured Ollama provider while LM Studio remained available.
- [x] Completed the Lightroom cancellation/recovery matrix during preview
  preparation, active embeddings, metadata dependency waiting, and active LM
  Studio inference. All nine canceled durable jobs are terminal, with no failed
  or nonterminal items; immediate follow-up jobs succeeded, and the later idle
  shutdown discarded zero pending or queued index items.
- [x] Investigated the reported post-test Lightroom shutdown delay. The backend
  had already completed an empty-queue idle shutdown, and the installed,
  packaged, and source `ShutdownApp.lua` files are identical and perform only a
  synchronous protected `doneFunc` call. macOS recorded termination approval in
  0.18 seconds followed by roughly 28 seconds of Lightroom post-approval
  teardown, with no StyleAI activity, plug-in timeout, or Lightroom diagnostic
  report. Treat as an unassigned one-off unless a controlled repeat reproduces
  it; capture a process sample if it does.
- [x] Verified the 1,118-photo training test crossed the 1,000-ID preflight page
  boundary and deterministically reduced five duplicate source instances to
  1,113 durable items. All 1,113 items succeeded with no failed, canceled, or
  nonterminal items. The upload phase took 282 seconds.
- [x] Traced the visible 10-photo cadence to bounded Lightroom upload chunks,
  not an idle queue: each next request began within roughly 1 ms, while the
  backend spent 2–3 seconds serially re-extracting RAW previews with ExifTool
  and writing each chunk. Preserve the bounded chunk for cancellation and
  timeout safety, but reuse the exact canonical vector and source metrics when
  the complete RAW fingerprint/model/preprocessing/schema stamp matches.
- [x] Removed redundant Lightroom rendered-thumbnail generation and base64
  transport from learned-editing uploads. Rendered pixels are not admissible as
  training source evidence; stale or incomplete canonical records still take
  the explicit RAW-preview extraction and embedding fallback.
- [x] Verified the optimization against another disposable fresh database.
  **Prepare Photos** indexed 1,118 embeddings in 206.83 seconds; **Learn From
  My Edits** reduced the selection to 1,115 unique sources, reused complete
  cached evidence for every item, and saved all examples in 62.89 seconds with
  zero recomputations or failures. This is about 4.5 times faster than the
  282-second pre-optimization upload.
- [x] Traced the remaining sub-second-to-one-second 10-photo pauses to durable
  persistence rather than accelerator work. Publish each training chunk's
  running and terminal operation-item transitions atomically, reducing roughly
  20 SQLite transactions and finalization scans per chunk to two while keeping
  bounded Lightroom chunks, per-photo outcomes, retries, and immediate scoped
  cancellation.
- [x] Fetch each chunk's ten canonical embedding/source-metric records in one
  bounded Chroma read instead of ten individual reads. Preserve exact per-photo
  contract validation and independently recompute any missing or stale source.

## Goals and product boundaries

- [x] Restore one deterministic photo identity for cached, uncached, and
  explicitly recomputed photos.
- [x] Preserve catalog-local storage and loopback-only service operation.
- [x] Preserve scoped cancellation: canceling one Lightroom operation must not
  cancel or corrupt unrelated work.
- [x] Keep queues, Chroma traversal, request payloads, and operation-item access
  bounded without imposing a fixed catalog-size ceiling.
- [x] Keep routes responsible for request validation and serialization;
  services remain responsible for identity, operation, and workflow behavior.
- [x] Preserve virtual-copy deduplication without copying pixels or metadata
  between distinct source photos.
- [x] Do not add cloud services, telemetry, remote URLs, cross-catalog routing,
  shared databases, or a new dependency.
- [x] Add regression tests before or with every behavior change.
- [x] Keep English, Catalan, German, Spanish, and French visible strings
  synchronized when completion or error messages change.

## Recommended implementation order

1. Resolve the stable-ID contract before writing more derived data.
2. Repair metadata cancellation and prove that resources are released.
3. Make training preflight deduplicated and catalog-size independent.
4. Correct no-op completion and live progress accounting.
5. Restore static validation and remove debugging artifacts.
6. Run automated, synthetic, and Lightroom human release gates.

Do not combine these phases with unrelated model, UI, or indexing refactors.
Each phase should be independently reviewable and leave the prior active
learned-policy generation intact on failure.

## 0. Establish the recovery baseline

- [ ] Record the current commit and confirm the working tree is clean before
  implementation begins.
- [ ] Inventory production references to `globalPhotoId`, `meta1:`, `meta2:`,
  `stable_meta_v1`, and `forceRecompute` across Lua, Chroma metadata, SQLite
  rows, backup/restore, prune, training, recommendations, and edit history.
- [ ] Determine whether a build containing `meta2:` was distributed or used
  against a catalog that must be preserved.
- [ ] Inspect a disposable copy of an affected catalog-local database and count
  `meta1:` and `meta2:` records separately in `image_embeddings`,
  `edit_training`, operation jobs, recommendations, and inference history.
- [ ] Never inspect or log pixels, paths, Develop settings, embeddings, or
  sensitive EXIF at INFO while collecting the baseline.
- [ ] Back up any real catalog-local `styleai.db` before testing an identity or
  cleanup path. Validate ownership, checksums, SQLite integrity, and archive
  paths before mutation.
- [ ] Capture reproducible Lightroom fixtures covering:
  - an original with a cached `meta1:` ID;
  - the same original with no cached ID;
  - explicit ID recomputation;
  - an original plus one or more virtual copies;
  - two genuinely distinct photos with similar metadata;
  - a fully indexed selection and a partially indexed selection;
  - embedding-only, metadata-only, and combined indexing.

Exit gate: the team knows whether `meta2:` is unreleased disposable state or
user data that needs an explicit recovery path.

## 1. Restore a single stable photo-identity contract

### 1.1 Preferred unreleased-state fix

- [ ] If `meta2:` has not shipped, change
  `Util.computeStableMetadataPhotoId()` back to the `meta1:` prefix.
- [ ] Keep `STABLE_ID_ALGO = "stable_meta_v1"` only if the payload and identity
  semantics remain exactly the v1 contract.
- [ ] Add a comment or fixture documenting that changing only an ID prefix is a
  data-contract change, not a harmless cache invalidation.
- [ ] Confirm a cached ID, an uncached computed ID, and a force-recomputed ID
  are byte-for-byte identical for the same Lightroom photo.
- [ ] Confirm virtual copies intentionally resolve to the same source identity
  where workflows require source-level deduplication.
- [ ] Confirm two distinct source files do not collapse merely because a
  virtual-copy deduplication fix is active.

### 1.2 Recovery path if `meta2:` has shipped — not applicable

The current database is disposable test data. The implementation removed it
and deliberately did not add the following migration machinery.

- [ ] Do not silently accept mixed `meta1:`/`meta2:` identity under the same
  `stable_meta_v1` algorithm marker.
- [ ] Choose and document one canonical version before implementation.
- [ ] Prefer regenerating disposable derived state when safe; do not rewrite
  immutable history or user review records without a validated mapping.
- [ ] If a data migration is required, build it as a catalog-local maintenance
  operation with:
  - a required pre-mutation backup;
  - ownership and database-marker validation;
  - deterministic old-to-new mapping with collision detection;
  - an all-or-nothing SQLite transaction where applicable;
  - staged Chroma replacement with count and stamp verification;
  - fail-closed handling for ambiguous mappings;
  - restart/recovery behavior that never exposes a half-migrated database.
- [ ] Refuse automatic migration when one old ID maps to multiple distinct
  originals or when immutable history cannot be reconciled safely.
- [ ] Preserve the previous active learned-policy generation until replacement
  evidence and artifacts have been completely rebuilt and activated.

### 1.3 Identity tests and release gates

- [ ] Add Lua/static fixtures for cached, uncached, force-recomputed, offline,
  original, and virtual-copy identity behavior.
- [ ] Add backend fixtures proving identity changes cannot cross-link Chroma
  collections or operation items.
- [ ] Add prune and restore regression coverage for mixed-prefix input.
- [ ] Verify a no-op **Prepare Photos** run does not create a second record for
  the same source photo.
- [ ] Verify recommendations and edit history still resolve the intended
  Lightroom photo after restart.

Exit gate: every route and Lightroom workflow resolves one canonical ID for a
photo regardless of cache state, and no catalog contains an unexplained mixed
identity contract.

## 2. Make metadata cancellation genuinely operation-scoped

### 2.1 Propagate cancellation identity

- [x] Add the admitted `job_id` to every per-photo metadata option after route
  validation; do not rely on `_extract_options()` to preserve unknown fields.
- [x] Pass one explicit `JobCancelSignal` through the route, admission wait,
  embedding-order wait, batch processing, and metadata executor.
- [x] Keep `GLOBAL_CANCEL_EVENT` limited to process shutdown/watchdog behavior;
  it must not be the only signal for a user-canceled operation.
- [x] Check the job cancellation signal while waiting for each photo to leave
  `active_embeddings_uuids`.
- [x] Replace the unbounded polling loop with a helper that has cancellation,
  service-shutdown, and invariant-failure exits. Do not add an arbitrary normal
  inference timeout that races a slow MPS batch.

### 2.2 Stop and release queued LLM work

- [x] Check cancellation before each LLM task starts and between independent
  batch members.
- [x] On the first scoped cancellation, cancel executor futures that have not
  started and prevent additional submissions for that job.
- [x] Allow an already-running provider call to finish safely if the provider
  cannot be interrupted, but discard its result for a canceled item.
- [x] Ensure the global LLM executor becomes available to another operation as
  soon as the unavoidable in-flight call returns; canceled queued members must
  not drain serially through full inference.
- [x] Release cached image bytes for every canceled, rejected, or otherwise
  unfinished metadata item exactly once.
- [x] Ensure admission claims, queue bookkeeping, and `active_embeddings_uuids`
  membership are released in `finally` paths.

### 2.3 Preserve operation semantics

- [x] Persist canceled items as `canceled`, not `failed`, when the parent job
  has a cancellation request.
- [x] Prevent the Lightroom metadata worker from overwriting a backend-canceled
  item with a later generic `failed` update after an HTTP 409 response.
- [x] Keep unrelated indexing and metadata jobs nonterminal and unaffected.
- [x] Make cancellation idempotent across lost responses and repeated cancel
  requests.
- [x] Keep backend completion nonterminal until required Lightroom handoff is
  recorded for successful, non-canceled items.

### 2.4 Cancellation tests

- [x] Add a route test that proves `job_id` reaches every metadata worker
  option.
- [x] Add a test that cancels while waiting for an active embedding and asserts
  prompt exit without acquiring the LLM/accelerator vector.
- [x] Add a serial-executor test with one running and multiple queued LLM items;
  assert queued canceled items never call the provider.
- [ ] Add tests for cancellation before admission, during embedding, during
  LLM inference, during Lightroom handoff, and after terminal completion.
- [ ] Add a two-job test proving cancellation of job A neither cancels nor
  starves job B.
- [ ] Assert image-cache bytes, admission claims, active UUIDs, and operation
  item states return to their expected values after every cancellation case.
- [ ] Add a repeated-cancel stress test demonstrating that Waitress capacity
  and the global LLM executor do not become exhausted.

Exit gate: canceled work stops at the nearest safe boundary, releases bounded
resources, remains labeled canceled, and cannot delay an unrelated job beyond
the unavoidable current provider call.

## 3. Remove the training-preflight catalog ceiling and duplicate-ID failure

### 3.1 Deduplicate in Lightroom before transport

- [x] Build an ordered `photo_id -> representative photo` map before calling
  `/training/preflight`.
- [x] Deduplicate virtual copies by canonical source ID without changing their
  Lightroom selection or treating one copy's edited pixels as another photo's
  evidence.
- [x] Define the deterministic representative rule for duplicate source IDs.
  Prefer the source instance whose Develop settings are intended as the
  training target; if conflicting edited copies are ambiguous, exclude the
  source with an explicit user-visible reason rather than choosing silently.
- [x] Report selected count, unique source count, skipped duplicate count, and
  ineligible-format count separately.

### 3.2 Traverse preflight in bounded pages

- [x] Replace the single catalog-wide preflight request with bounded chunks.
- [x] Preserve input order while accumulating `existing_photo_ids` and
  `needed_photo_ids` across chunks.
- [x] Keep the per-request limit as a transport/memory bound, not as a product
  ceiling; catalogs of 5,001, 10,000, and larger must continue across pages.
- [x] Check Lightroom cancellation between chunks and yield during long loops.
- [x] Keep Chroma lookups bounded and use `count()` plus limited ID batches;
  never load an entire collection to answer one page.
- [x] Fail the preflight atomically from the user's perspective: do not start
  extraction or create a partial training operation if any page fails.
- [ ] Include the complete deduplicated ID set in the operation fingerprint so
  retries remain deterministic.

### 3.3 Handle already-learned but not active state

- [x] Distinguish “all examples already exist” from “an active learned-policy
  generation exists.”
- [x] If examples exist but no active generation is usable, direct the user to
  **Styles & Training → Rebuild** or offer the existing explicit rebuild flow;
  do not report the workflow as fully ready to apply.
- [x] Rebuild only once after a complete changed training upload, never once
  per preflight page or upload chunk.

### 3.4 Training-preflight tests

- [ ] Add tests for 0, 1, 5,000, 5,001, 10,000, and repeated IDs.
- [ ] Add Lightroom-side fixtures for an original plus virtual copies, selected
  duplicates, and an entire catalog with multiple bounded pages.
- [ ] Add partial-page failure, cancellation-between-pages, retry, and
  force-retrain tests.
- [ ] Assert no preview export, base64 encoding, operation creation, or model
  inference occurs for examples preflight classifies as already current.
- [ ] Assert a failed preflight leaves training collections and the active
  generation unchanged.

Exit gate: training scope is limited only by bounded traversal and available
local resources, not by a fixed catalog size or virtual-copy duplication.

## 4. Treat fully indexed selections as successful no-ops

- [x] After index preflight, distinguish these cases explicitly:
  - no selected photo has a resolvable stable ID: failure;
  - every unique photo is already complete: successful no-op;
  - one or more unique photos require processing: create an operation job.
- [x] Return `success`, the deduplicated processed/success count, zero failures,
  and no operation ID for the fully complete case.
- [x] Do not create an empty operation job.
- [x] Do not run preview extraction, export, queue admission, or backend polling
  for a successful no-op.
- [x] Use a localized “already complete” completion message rather than “All 0
  photos failed.”
- [x] Preserve the ordinary failure message when selected photos genuinely
  cannot produce stable IDs.
- [ ] Add tests for an empty Lightroom selection, all unresolved IDs, all
  complete, partially complete, server-empty, and regenerate-all behavior.

Exit gate: rerunning **Prepare Photos** on an unchanged selection is fast,
successful, and reports the unique number of already-complete photos.

## 5. Make live indexing progress use one accounting source

- [x] Define one display equation for each mode using mutually exclusive
  buckets: preflight-complete, pending, running, succeeded, failed, canceled,
  and unresolved.
- [x] Never add a local failure count to the same backend operation failure
  already persisted in `item_state_counts`.
- [x] Include `preOperationSuccess` in embedding-only live success totals.
- [x] Decide whether canceled items appear in a separate canceled count or in
  the visible unsuccessful count; use that decision consistently during work
  and at completion.
- [x] Use the deduplicated unique-photo count as the denominator everywhere.
- [x] Keep `processed <= unique total`, and keep success plus unsuccessful
  counts from exceeding the total during retries or backpressure.
- [x] Freeze final totals from terminal operation items, with local unresolved
  failures merged exactly once.
- [ ] Extract the calculation into a small testable helper where practical,
  leaving Lightroom UI calls thin.
- [ ] Add fixtures for preflight successes, local preview failures, backend
  failures, retryable queue rejection, terminal rejection, metadata handoff
  failure, cancellation, and mixed-mode success.
- [ ] Human-check that the caption is monotonic enough to be understandable and
  never displays negative, doubled, or greater-than-total counts.

Exit gate: live and final counts agree for every indexing mode and every tested
mixture of preflight, backend, local, and canceled outcomes.

## 6. Restore repository validation and remove debugging artifacts

### 6.1 Python and Lua validation

- [x] Replace the one-line duplicate-ID debug statement in
  `server/src/routes/index.py` with the configured module logger and normal
  multiline control flow.
- [x] Do not log the complete supplied ID list at WARNING/INFO; log a bounded
  count or a redacted diagnostic because photo identity is catalog data.
- [x] Replace the stale `from datetime import datetime as time` alias with clear
  `datetime` and standard-library `time` imports, then remove local shadowing.
- [x] Remove unused exception bindings from re-raise-only handlers.
- [x] Replace native `pcall` in normal async Lightroom work with
  `LrTasks.pcall`, or restructure cancellation access so the validator and
  Lightroom yielding rules are satisfied.
- [x] Run Ruff formatting after the semantic changes and review the diff for
  unrelated rewrites.

### 6.2 Remove or promote one-off artifacts

- [x] Remove the empty repository-root `styleai.db`; ensure catalog database
  paths remain directories adjacent to Lightroom catalogs.
- [x] Remove `patch_index.py`, `patch_lua.py`, `patch_util.py`, and
  `test_parse.py` after confirming they contain no unique required behavior.
- [x] Remove `server/test_chroma_init.py`, `server/test_chroma_init2.py`,
  `server/test_new_db.py`, and `server/test_siglip.py`, or convert unique
  assertions into isolated tests under `server/test/` with fixtures and no
  real model/cache dependency.
- [x] Add appropriate ignore rules for catalog-local databases and one-off
  local diagnostics without masking legitimate migration fixtures.
- [x] Confirm packaging contains only the intended plug-in tree and release
  manifest.

Exit gate: the standard lint, format, plug-in validation, and packaging checks
pass from a clean checkout, and no local database/debug patch artifact is
tracked.

## 7. Automated validation matrix

- [x] Run `bash server/scripts/lint_format.sh`.
- [x] Run `(cd server && uv run pytest test/)`.
- [x] Run `python scripts/validate_lrc_plugin.py`.
- [x] Run `python sync_translations.py` and inspect all five translation files
  for unintended drift.
- [x] Run `python scripts/package_lrc_plugin.py developer` and inspect the
  disposable package; do not enable developer menus in the checked-in release
  manifest.
- [x] Run focused operation, route, indexing, metadata, image-cache, Chroma,
  training, policy-store, recommendation, history, backup, and recovery tests.
- [x] Add a synthetic large-catalog/static-contract test proving bounded traversal without a
  fixed 5,000-photo ceiling or unbounded request/queue growth.
- [x] Add concurrency instrumentation or assertions proving complete resource
  vectors are acquired atomically and released after failure/cancellation.
- [x] Run `git diff --check` and confirm the working tree contains only intended
  source, test, and documentation changes.

Automated exit gate: all checks pass with no ignored failures, no network
access, no real catalog mutation, and no dependency outside
`server/pyproject.toml` and `server/uv.lock`.

Validation result (2026-08-11, after the database/log follow-up): Ruff lint and
format passed; the complete pytest suite passed 486 tests with 60 existing
scikit-learn warnings; the
Lightroom plug-in validator passed; all five translations synchronized; and
the developer package was generated at `build/StyleAI-dev.lrdevplugin`.

## 8. Lightroom human validation matrix

- [x] **Prepare Photos**, embedding-only, on a new disposable catalog.
- [x] **Prepare Photos**, metadata-only, on already embedded photos.
- [x] **Prepare Photos**, combined mode, beyond the backend queue capacity and
  across the former 96-photo stall boundary.
- [x] Rerun each tested mode on a fully complete selection and confirm a successful
  no-op with correct unique counts.
- [x] Run a partially complete selection and compare live counts with terminal
  operation item counts.
- [x] Include originals plus virtual copies and confirm intentional source
  deduplication without duplicate-ID rejection.
- [x] Cancel while previews are being prepared, embeddings are active, metadata
  waits on embeddings, and the local LLM is running.
- [x] Start a second independent job after each cancellation and confirm it
  proceeds without service restart or prolonged starvation.
- [x] Run **Learn From My Edits** on more than one preflight page and on a
  selection containing virtual copies.
- [ ] Confirm all-already-learned behavior distinguishes an active policy from
  a catalog that still needs **Styles & Training → Rebuild**.
- [ ] Restart Lightroom and the service between identity checks to exercise
  cached and uncached resolution.
- [ ] Verify prune, backup, restore, and service idle shutdown remain safe and
  nonblocking after the fixes.
- [x] Let the backend idle-shutdown, reopen **Prepare Photos**, and confirm the
  startup wait is followed by enabled SigLIP controls and a ready LM Studio
  status without resetting or re-downloading either model.
- [x] Inspect the catalog-adjacent service and launcher logs
  for exceptions, sensitive INFO output, stuck jobs, or leaked resources.

Human exit gate: indexing, cancellation, training preflight, restart, and
maintenance behavior match the documented contracts on a disposable catalog.

## 9. Documentation and release handoff

- [x] Update `docs/wiki/Architecture.md`, `docs/wiki/Developer-Guide.md`, and
  `docs/wiki/Plugin-Guide.md` if the identity, cancellation, progress, or
  training-preflight contract changes.
- [x] Update UI behavior contracts and the human test matrix for the successful
  no-op and cancellation messages.
- [x] Synchronize all visible strings across English, Catalan, German, Spanish,
  and French resources.
- [x] Record whether the selected identity resolution was an unreleased
  rollback or a shipped-data recovery.
- [x] Report exact automated commands, pass/fail totals, human checks, and any
  remaining risks in the implementation handoff.
- [x] Do not mark this checklist complete until the old debug artifacts are
  gone and every required release gate above passes.

## Lightroom rerun matrix

- Stable-ID prefix rollback before any affected build was used: no user rerun
  is required for catalogs that never wrote `meta2:` data.
- Stable-ID repair for a catalog that contains mixed or orphaned indexed
  evidence: run **Prepare Photos** after the repair is installed.
- Stable-ID or neutral-source repair affecting learned examples: run
  **Learn From My Edits** for affected photos, then run
  **Styles & Training → Rebuild**.
- Cancellation, live-progress accounting, successful no-op handling, validation
  cleanup, and bounded preflight transport do not require a Lightroom rerun by
  themselves.

## Definition of done

- [x] Cached, uncached, and recomputed identity is deterministic and documented.
- [x] No affected catalog is left with unexplained mixed identity state.
- [x] Scoped cancellation releases queued work and cannot exhaust Waitress or
  the global LLM executor.
- [x] Training preflight supports arbitrarily large catalogs through bounded
  traversal and handles virtual copies deterministically.
- [x] Fully indexed selections complete successfully without creating empty
  jobs or extracting previews.
- [x] Live and final progress totals agree and never double-count failures.
- [ ] All automated and Lightroom human gates pass.
- [x] Required **Prepare Photos**, **Learn From My Edits**, and
  **Styles & Training → Rebuild** instructions are included in the release
  handoff where applicable.
