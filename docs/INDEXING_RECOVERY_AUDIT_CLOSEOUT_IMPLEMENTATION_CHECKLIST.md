# Indexing Recovery Audit Closeout Implementation Checklist

Status: complete and closed on August 12, 2026. Implementation, automated
validation, packaging, deployment, and Lightroom acceptance all passed.

This checklist supersedes
`INDEXING_RECOVERY_AUDIT_IMPLEMENTATION_CHECKLIST.md` for all remaining work.
The earlier document remains an immutable record of the original audit,
implementation sequence, database findings, and completed Lightroom tests. Do
not reopen its historical or explicitly inapplicable boxes. This closeout plan
carries forward only verified unfinished work.

## Closeout outcome

- [x] Data & Recovery actions launched from Plug-In Manager cannot be submitted
  twice while one is active.
- [x] Confirmation and completion state remain visible and actionable without a
  dialog becoming hidden behind Plug-In Manager.
- [x] Training operation fingerprints deterministically cover the complete
  deduplicated request, including photos skipped as already current.
- [x] The highest-value cancellation, training-preflight, no-op, and progress
  accounting gaps have explicit automated regression coverage.
- [x] The old audit is classified as historical/superseded, and this checklist
  is the sole source of truth for closeout status.
- [x] All focused, full-suite, plug-in, packaging, translation, and Lightroom
  acceptance gates pass.

## Scope and non-goals

- [x] Preserve all currently validated indexing, training, backup, restore,
  prune, cancellation, shutdown, and learned-policy behavior.
- [x] Do not change stable photo identity, source evidence, EXIF extraction,
  Chroma schema, SQLite schema, model artifacts, policy discovery, or
  recommendation admission.
- [x] Do not add a database migration. The prior `meta2:` recovery branch was
  explicitly inapplicable because the affected database was disposable and no
  such build shipped.
- [x] Do not resume the shelved general performance-optimization checklist.
- [x] Do not add dependencies, cloud services, telemetry, remote URLs, or
  cross-catalog behavior.
- [x] Keep changes independently reviewable: UI maintenance safety, operation
  fingerprints, tests, and documentation should not require one another to
  preserve existing data.

## 0. Establish the closeout baseline

- [x] Record the current commit and `git status --short`; preserve all existing
  user changes and the untracked `backup_test.zip`.
- [x] Run `git diff --check` before editing and distinguish pre-existing changes
  from closeout changes.
- [x] Re-run the current focused contracts before behavior changes:
  - `server/test/test_lua_workflow_contracts.py`;
  - `server/test/test_plugin_packaging.py`;
  - `server/test/test_operations.py`;
  - `server/test/test_routes_index_batch_base64.py`;
  - `server/test/test_routes_training.py`;
  - `server/test/test_server_lifecycle.py`.
- [x] Inspect the current Plug-In Manager action flow in
  `PluginInfoDialogSections.lua`, `TaskPruneDatabase.lua`, and the backup,
  restore, and prune API helpers before choosing UI mechanics.
- [x] Inspect operation creation, fingerprint matching, retry, and coalescing in
  `TaskTrainFromEdits.lua`, `APISearchIndex.lua`, and
  `services.operations` before defining the training fingerprint contract.

Exit gate: the implementation begins from a known passing baseline, and no
unrelated worktree content is overwritten or reformatted.

## 1. Make Data & Recovery actions single-flight and visible

### 1.1 Define one Plug-In Manager maintenance state

- [x] Add one bound `dataRecoveryBusy` property owned by the Plug-In Manager
  property table; initialize it to `false` every time the panel is constructed.
- [x] Add one bound `dataRecoveryStatus` string for concise inline state and
  completion/error summaries.
- [x] Disable **Export Backup**, **Restore Backup**, and **Clean Up Removed
  Photos** while any one of those actions is active.
- [x] Set the busy state before starting asynchronous work, not inside the task,
  so a fast second click cannot race task startup.
- [x] Clear the busy state in a single `finally`-equivalent path after success,
  cancellation, or error.
- [x] Keep backend maintenance/catalog-write admission as the authoritative
  cross-workflow safety boundary; UI disabling is an additional user-facing
  guard, not a replacement for backend serialization.

### 1.2 Keep confirmation and completion visible

- [x] Initiate destructive confirmation directly from the button action while
  Plug-In Manager owns focus; do not first enter a nested asynchronous task that
  can place the confirmation behind its parent window on macOS.
- [x] Capture all user choices required by export/restore before starting the
  long asynchronous phase.
- [x] Prefer bound inline completion status inside the Data & Recovery section
  for Plug-In Manager actions instead of an asynchronous completion message
  that can be obscured by Plug-In Manager.
- [x] Preserve critical error visibility through the established error handler,
  while also updating inline status so the result remains discoverable after
  window focus changes.
- [x] Ensure completion text reports the same checked, removed, disassociated,
  backup, and restore results currently shown to the user.
- [x] Do not expose catalog paths, photo IDs, metadata, or archive internals in
  routine status text or INFO logging.

### 1.3 Refactor prune task ownership without changing prune semantics

- [x] Remove nested `LrTasks.startAsyncTask` ownership between the Plug-In
  Manager button and `TaskPruneDatabase.process()`.
- [x] Give exactly one layer responsibility for asynchronous execution.
- [x] If `TaskPruneDatabase` remains reusable, accept explicit callbacks or
  options for confirmation, busy state, status, and completion presentation;
  do not couple the task directly to Plug-In Manager internals.
- [x] Preserve the empty-catalog safety rejection, pre-prune backup,
  cancellation during catalog traversal, bounded yielding, and backend prune
  response contract.
- [x] Prevent double completion, double error reporting, or a busy flag left set
  when the user cancels confirmation or a file chooser.

### 1.4 UI regression coverage

- [x] Add a static Lua workflow contract proving all three maintenance buttons
  bind their enabled state to the shared busy property.
- [x] Add a contract proving busy state is set before task launch and cleared on
  every terminal path.
- [x] Add a contract proving prune confirmation is not launched from a nested
  asynchronous task.
- [x] Preserve localization for every new visible status string and synchronize
  English, Catalan, German, Spanish, and French resources.

Exit gate: one maintenance action can run at a time, its result remains visible
inside Plug-In Manager, and no confirmation can strand the user behind an
unfocusable parent window.

## 2. Add a deterministic training-operation fingerprint

### 2.1 Define the canonical payload

- [x] Compute the fingerprint only after stable-ID resolution and source-level
  deduplication are complete.
- [x] Include the complete deduplicated ID set from the request, not only the
  subset that preflight says needs upload.
- [x] Copy and lexicographically sort IDs solely for hashing; preserve original
  Lightroom order and representative-photo selection for actual processing.
- [x] Include an explicit fingerprint schema/version field, operation kind,
  normalized scope, and `force_retrain` boolean.
- [x] Include only fields that change the semantic training request. Do not add
  UI captions, timestamps, selection order, paths, Develop values, pixels,
  embeddings, or transient resource limits.
- [x] Use the existing canonical JSON and MD5 mechanism already used for
  indexing-operation fingerprints unless inspection shows a compatibility
  reason to extract a shared helper.
- [x] Document why already-current IDs remain in the fingerprint: retries of
  the same user request must retain identity even if preflight state changes
  between attempts.

### 2.2 Preserve operation and policy behavior

- [x] Pass the fingerprint into `SearchIndexAPI.startOperation("training", ...)`
  instead of `nil`.
- [x] Keep operation item rows limited to photos actually needing work; the full
  request belongs in the fingerprint/details, not as false pending items.
- [x] Record deduplicated requested, existing, needed, duplicate-source, and
  ineligible counts in bounded operation details where useful for auditability.
- [x] Confirm whether training should continue using `coalesce = false`. Do not
  enable coalescing merely because a fingerprint now exists.
- [x] Verify same-request retries produce the same fingerprint and changed ID,
  scope, force-retrain, or fingerprint-schema inputs produce a different one.
- [x] Confirm a reordered selection with the same canonical source IDs produces
  the same fingerprint without changing representative selection behavior.

### 2.3 Fingerprint regression coverage

- [x] Add a Lua/static fixture for empty, one-ID, repeated-ID, reordered-ID, and
  multi-page deduplicated requests.
- [x] Assert the complete preflight input set is fingerprinted even when every
  example is already current or only one item needs upload.
- [x] Assert duplicate virtual-copy source IDs appear exactly once.
- [x] Assert force-retrain and scope changes affect the fingerprint.
- [x] Assert transient preflight classification and selection order do not
  affect the fingerprint.
- [x] Add or extend backend operation tests to ensure fingerprints do not cause
  unintended coalescing or cross-link operation items.

Exit gate: training retries have deterministic request identity without
changing saved examples, model input, operation membership, or rebuild timing.

## 3. Close the highest-value automated test gaps

### 3.1 Scoped cancellation and resource release

- [x] Add a two-job test in which job A is canceled while queued or running and
  job B becomes eligible immediately after the unavoidable in-flight provider
  call; assert A cannot cancel, starve, or mutate B.
- [x] Cover cancellation before admission, while waiting on embedding order,
  with queued LLM futures, during one in-flight LLM call, during Lightroom
  handoff, and after terminal completion.
- [x] At every boundary, assert image-cache bytes, executor futures, admission
  claims, active embedding UUIDs, workflow gates, and operation-item states
  return to expected values.
- [x] Add a repeated-cancel stress test large enough to expose leaked Waitress
  or executor capacity without loading real models or using network access.
- [x] Assert late worker publication remains idempotent after cancellation and
  cannot reopen terminal items.

### 3.2 Training preflight and failure atomicity

- [x] Retain the existing synthetic 0, 1, 5,000, 5,001, and 10,000-photo bounded
  traversal contract and relabel the corresponding old checklist items as
  superseded rather than reimplementing them.
- [x] Add behavior fixtures for partial-page failure, cancellation between
  pages, retry, force-retrain, and repeated source IDs.
- [x] Assert a failed preflight creates no operation, exports no preview,
  performs no base64 encoding or model inference, and leaves training
  collections and the active policy generation unchanged.
- [x] Assert all-current preflight performs no extraction or model work and
  preserves the active-generation versus needs-rebuild message distinction.

### 3.3 Index no-op and progress accounting

- [x] Add fixtures for empty selection, all unresolved IDs, all complete,
  partially complete, server-empty, and regenerate-all behavior.
- [x] Extract pure progress arithmetic into a small Lua helper only if it can be
  done without changing UI timing or workflow ownership; otherwise test the
  existing calculation through a static or table-driven contract.
- [x] Cover preflight successes, unresolved/local failures, retryable queue
  rejection, terminal backend failure, metadata handoff failure, cancellation,
  and mixed embedding/metadata success.
- [x] Assert every intermediate and final state satisfies `processed <= total`,
  contains no negative bucket, and counts each photo in at most one terminal
  bucket.
- [x] Assert live final totals match terminal operation-item counts plus local
  unresolved failures merged exactly once.

Exit gate: the prior human cancellation/progress matrix is backed by focused,
deterministic automated tests for concurrency and accounting regressions.

## 4. Automated validation

- [x] Run focused tests after each phase rather than waiting for the full suite.
- [x] Run `bash server/scripts/lint_format.sh`.
- [x] Run `(cd server && uv run pytest test/)` and record the exact pass count
  and warnings.
- [x] Run `python scripts/validate_lrc_plugin.py`.
- [x] Run `python sync_translations.py` and inspect all five translations for
  unintended drift.
- [x] Build both disposable packages:
  - `python scripts/package_lrc_plugin.py developer`;
  - `python scripts/package_lrc_plugin.py release`.
- [x] Validate both generated packages and confirm release manifests contain no
  developer-only menu entries or shutdown hook.
- [x] Run `git diff --check` and review the complete diff for unrelated changes,
  sensitive logging, and accidental database/archive inclusion.

Recorded automated result (August 12, 2026): 522 tests passed with 71 warnings
(65 established scikit-learn constant-residual warnings and six joblib
physical-core-detection warnings). Source, developer package, and release
package validation passed; translation synchronization and lint/format checks
were clean.

Automated exit gate: all checks pass with no ignored failures, network access,
real catalog mutation, new dependency, or sensitive diagnostic output.

## 5. Lightroom acceptance matrix

Use the disposable test catalog/database. A database reset is not required
unless a test itself corrupts disposable state.

### 5.1 Data & Recovery UI

- [x] Open Plug-In Manager → StyleAI → Data & Recovery.
- [x] Start **Export Backup** and immediately try all three maintenance buttons;
  confirm no second action starts and visible status progresses to completion.
- [x] Cancel the export chooser and confirm controls re-enable with no success
  status or backend operation.
- [x] Start **Restore Backup**, cancel confirmation, and then cancel archive
  selection; confirm controls re-enable after both paths.
- [x] Complete a restore and confirm success remains visible above/in Plug-In
  Manager without searching for a hidden dialog.
- [x] Start **Clean Up Removed Photos**, verify its confirmation is frontmost,
  and attempt rapid repeat clicks; confirm exactly one prune and one pre-prune
  backup occur.
- [x] Cancel prune while scanning a sufficiently large catalog and confirm the
  UI returns to idle without pruning or leaving maintenance admission held.
- [x] Confirm Plug-In Manager can be closed normally after every success,
  cancellation, and error path.

### 5.2 Training fingerprint behavior

- [x] Run **Learn From My Edits** on a selection containing originals, virtual
  copies, already-current examples, and at least one new example.
- [x] Confirm saved, skipped-existing, duplicate-source, and total counts remain
  unchanged from established behavior.
- [x] Retry the unchanged selection and confirm the all-current result remains
  immediate and creates no empty training operation.
- [x] Enable **Update previously learned examples** and confirm the operation
  runs normally and rebuilds once after the complete upload.
- [x] Inspect only bounded operation metadata/logging needed to confirm stable
  fingerprints; do not log photo IDs or source data at INFO.

### 5.3 Cancellation and progress spot checks

- [x] Run one combined indexing cancellation followed immediately by an
  unrelated embedding-only job; confirm the second job proceeds.
- [x] Run one fully complete no-op and one partially complete selection; confirm
  visible totals are monotonic and match final results.
- [x] Quit Lightroom once after maintenance/testing and confirm prompt shutdown
  with clean backend marker/port state.

Human exit gate: maintenance UI cannot hide or duplicate actions, training
behavior is unchanged apart from deterministic operation identity, and the
existing indexing/cancellation workflows remain regression-free.

## 6. Documentation and supersession cleanup

- [x] Keep the previous audit document unchanged except for its supersession
  notice and link to this checklist.
- [x] Classify its unchecked items as follows in the final handoff rather than
  editing historical evidence:
  - superseded by completed outcome/human validation;
  - explicitly not applicable (`meta2:` migration);
  - carried forward into this checklist;
  - historical prerequisite that cannot be performed retroactively.
- [x] Update the Developer Guide for Plug-In Manager maintenance single-flight
  behavior and training fingerprint semantics if implementation changes a
  documented contract.
- [x] Record exact files changed, validation commands, automated pass count,
  Lightroom results, and any remaining risks.
- [x] Mark the old audit closed/superseded and this checklist complete only when
  all applicable automated and Lightroom gates above pass.

## Lightroom rerun requirements

These changes do not alter source evidence, grouping, EXIF extraction, database
schema, or learned models. Existing users do not need to rerun **Prepare
Photos**, **Learn From My Edits**, or **Styles & Training → Rebuild** merely to
install the closeout changes.

The Lightroom training run in section 5 is acceptance testing only. A rebuild
is expected only when that test deliberately uploads changed examples, matching
normal existing behavior.

## Definition of done

- [x] Data & Recovery actions are single-flight and their status cannot be
  hidden behind Plug-In Manager.
- [x] Training fingerprints cover the canonical complete deduplicated request
  and are stable across order/preflight changes.
- [x] Scoped cancellation and progress invariants have the focused automated
  coverage defined above.
- [x] Full validation and packaging pass.
- [x] Lightroom acceptance tests pass on the disposable catalog.
- [x] The prior audit is clearly superseded and contains no ambiguous live work.
- [x] No applicable item in this checklist remains unchecked.

## Final acceptance record

Lightroom acceptance completed on August 12, 2026. The user confirmed the full
matrix passed after the completion presentation was strengthened from inline
status alone to a four-second Lightroom bezel plus durable inline summary.
Read-only log review corroborated the final workflow sequence: 497 refreshed
training examples completed; a combined indexing job canceled cleanly; the
next 17-photo indexing job succeeded with zero failures; and its immediate
all-current rerun reported 17 complete with zero failures. Restore and cleanup
had previously been corroborated in the service log, including a zero-change
cleanup that checked 13,403 records and created its pre-prune backup.

Final automated validation recorded above remains 522 tests passed with 71
established warnings. Subsequent presentation-only corrections passed 25 and
13 focused tests respectively, lint/format, translation synchronization,
source validation, disposable package validation, and deployed-file/source
comparison.

Implementation commit `3e67ddc` changed the following exact files:

- `docs/INDEXING_RECOVERY_AUDIT_CLOSEOUT_IMPLEMENTATION_CHECKLIST.md`,
  `docs/INDEXING_RECOVERY_AUDIT_IMPLEMENTATION_CHECKLIST.md`,
  `docs/wiki/Developer-Guide.md`, and `docs/wiki/Plugin-Guide.md`;
- `plugin/StyleAI.lrdevplugin/APISearchIndex.lua`,
  `PluginInfoDialogSections.lua`, `ProgressAccounting.lua`,
  `TaskPruneDatabase.lua`, `TaskTrainFromEdits.lua`, and
  `TrainingPreflight.lua`;
- `plugin/StyleAI.lrdevplugin/TranslatedStrings_ca.txt`,
  `TranslatedStrings_de.txt`, `TranslatedStrings_en.txt`,
  `TranslatedStrings_es.txt`, and `TranslatedStrings_fr.txt`;
- `server/test/test_lua_progress_accounting.py`,
  `test_lua_training_preflight.py`, `test_lua_workflow_contracts.py`,
  `test_operations.py`, and `test_service_metadata.py`.
