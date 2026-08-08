# Developer Guide

Welcome to the StyleAI Developer Guide! This document provides upstream maintainers and curious contributors with an overview of the recent massive architectural refactoring, and details on how to build and extend the plugin.

## 0. Lightroom packages

The checked-in `plugin/StyleAI.lrdevplugin` tree is the release source and does
not register developer Help commands. Build a disposable package with literal
developer menu registrations by running:

```bash
python scripts/package_lrc_plugin.py developer
```

The output is `build/StyleAI-dev.lrdevplugin`. Use
`python scripts/package_lrc_plugin.py release` to stage a production
`build/StyleAI.lrplugin`. Generated packages are ignored; never toggle the
checked-in release manifest for local testing.

## 1. Global API Envelope

Every single communication between the Lua plugin and the Python backend is strictly wrapped in a JSON envelope. This ensures the frontend never crashes due to unhandled API changes and allows graceful surfacing of warnings.

**Standard Response Format:**
```json
{
  "results": { ... },
  "error": null,
  "warning": "Optional warning string that Lightroom should display."
}
```
*When adding new endpoints to `server/src/routes/`, ALWAYS use the `@api_envelope` decorator (or manually wrap your response).*

## 2. Asynchronous Lua Pipeline (`Pipeline.lua`)

For simple sequential Lightroom loops, shared error/progress handling is
available in `plugin/StyleAI.lrdevplugin/Pipeline.lua`.

**Key Features:**
- `Pipeline.runSequentialBatch(photos, progressScope, options, processFn)`
- Automatically wraps your `processFn` in an `LrTasks.pcall` to catch native crashes.
- Collects and tabulates successes and errors, returning them in a unified summary structure.
- Use it when its sequential contract fits. GPU/LLM/indexing workflows instead
  use durable backend operation jobs and bounded producer/consumer orchestration.

## 3. SQLite Schema Migrations (`migrations/`)

StyleAI now uses a custom, lightweight Python migration engine to manage SQLite schema evolution without relying on heavy frameworks like Alembic.

**How to add a database column:**
1. Create a new Python file in `server/src/migrations/versions/` named `00X_description.py`.
2. Define a single `def upgrade(conn: sqlite3.Connection):` function.
3. Use the `conn` object to execute your `ALTER TABLE` statements.
4. The background service will automatically apply it the next time Lightroom binds.

## 4. ML vs LLM Abstraction (`providers/`)

With the shift away from generative models to mathematically robust machine learning for local edits, the architecture is now strictly bifurcated:

- **Predictive ML (Core):** Powered by editing-policy v2: target-behavior mixture discovery, embedding-only source-space gates, burst-grouped estimator selection (reduced-rank ridge, weighted PLS, or multi-task Elastic Net), and shrunken hierarchical camera/profile calibration. It predicts absolute Lightroom targets and abstains on ambiguous membership.
- **Generative LLMs (Fallback/Metadata):** LLMs are used for zero-shot "Creative" fallback edits and generating semantic metadata (auto-tagging). Providers are restricted to locally running open-weights models through Ollama and LM Studio; do not add cloud providers, API-key storage, or remote backend support.

## 5. Security & Credentials

- **Backend Binding:** In production, the Flask background service unconditionally binds to `127.0.0.1` to prevent network exposure.
- **Local-only providers:** Do not add API-key storage, cloud providers, remote backends, or cloud-oriented privacy workarounds.

## 6. Observability

- **Diagnostic Reports:** Instead of asking users to zip up `.log` files, they can click "Generate Diagnostic Report" in the Lightroom Plugin Manager. This uses `TaskDiagnostics.lua` to pull backend `/health` and `/logs`, rendering a beautiful HTML file for instant browser viewing.

## 7. Pitfalls & C-Stack Overflows

Lightroom SDK plugins are prone to C-stack overflows if coroutine yields and database transactions are mismanaged.
- **NEVER yield inside a database transaction:** Do not call `LrTasks.yield()` inside `withWriteAccessDo` or `withPrivateWriteAccessDo` closures. Doing so suspends the Lua coroutine while holding a C-level SQLite transaction lock. The orphaned C-stack frames will rapidly accumulate and crash Lightroom.
- **The macOS Spin-Lock:** Using the pattern `if MAC_ENV then LrTasks.yield() else LrTasks.sleep(0.1) end` is a dangerous anti-pattern. On macOS, `LrTasks.yield()` without a subsequent sleep does NOT reliably return control to the Lightroom UI loop. During heavy batch processing, this causes C-stack buildup. ALWAYS use `LrTasks.yield(); LrTasks.sleep(0.01)` regardless of OS to guarantee the transaction stack flushes.
- **Transaction Bloat:** When applying multiple edits or metadata properties to a photo, combine them into a single `withWriteAccessDo` block. Firing multiple sequential database transactions per-photo exponentially increases SQLite overhead and stack pressure during batch operations.
- **Native `pcall` in Shutdown/Teardown:** Shutdown hooks are the exception: use native `pcall(doneFunc)` because Lightroom's async scheduler is unreliable during teardown. Keep the hook free of HTTP, filesystem, logging, and process-launch work; the backend performs its own bounded idle shutdown. Use `LrTasks.pcall` for normal asynchronous plugin work.

## 8. Python Backend Memory Efficiency

When processing images in Python, particularly 1024px JPEGs received from Lightroom:
- **Lazy Downsampling:** ALWAYS call `image.thumbnail()` BEFORE calling `.convert("RGB")`. Calling `.convert("RGB")` on a full-resolution JPEG forces Pillow to decode and allocate the entire uncompressed 3-channel array in memory (which can consume hundreds of megabytes) before downsizing. `thumbnail()` allows Pillow to decode natively at a lower resolution, drastically reducing RAM footprints.
- **16-bit TIFF Bracketing:** The AI Edit bracket generation works with 16-bit ProPhoto RGB TIFFs. To prevent Out-Of-Memory (OOM) crashes during concurrent processing, TIFF concurrency is strictly capped to the number of CPU threads via `BRACKET_SEMAPHORE` in `image_processing.py`.

## 9. Lightroom SDK Layout Quirks

The Lightroom SDK `LrView` engine has several undocumented layout quirks, particularly when attempting to align different components like `popup_menu` and `simple_list`:
- **`share()` Truncation:** Using `width = share("groupName")` on mixed components can cause the layout engine to collapse to the narrowest intrinsic width among the shared elements. For example, a `popup_menu` will shrink to the width of its currently selected item, aggressively truncating the contents of a `simple_list` sharing the same width group.
- **`width_in_chars` on `simple_list`:** The `simple_list` component often completely ignores the `width_in_chars` parameter, collapsing to the natural width of its text content even if `width_in_chars` is generously set.
- **The Solution:** To guarantee perfectly aligned widths between different UI elements (e.g., centering a dropdown directly above a list), bypass dynamic text-width logic and hardcode an explicit pixel width (`width = 600`). This ensures all components stretch symmetrically regardless of their intrinsic text contents.

## 10. Editing-Policy Discovery and Recommendations

- Do not restore hard-coded genre buckets, semantic genre caches, or keyword
  exception ladders. Subject and lighting diversity belong inside conditional
  policies, not in Cartesian style IDs.
- Do not add fixed CLIP text-probe scene taxonomies as a shortcut for training
  labels. Preserve user-authored open-vocabulary descriptors and provenance.
- Discover policies from differences in absolute edited targets, then require
  those components to be recognizable from source embeddings and pixel/EXIF
  evidence alone.
- Keep only HDR/profile as hard compatibility partitions. Camera body, lens,
  and other categories use shrunken calibration offsets.
- Add experts only after grouped held-out validation demonstrates material
  improvement, adequate effective support, and stable low-ambiguity assignment.
- Retrieve upgrade candidates from bounded multi-anchor Chroma neighborhoods.
  Membership precision is an admission gate; coverage and quality only rank
  candidates that already pass.
- Exclude panoramas in both training and recommendations through
  `photo_constraints.is_stitched_panorama`.

## 10.1 Operation and Resource Coordination

- Long-running work uses catalog-local operation jobs with per-photo terminal
  state and scoped cancellation. Global cancellation is only for shutdown.
- Backend work that still requires a Lightroom metadata or Develop handoff
  remains nonterminal. Lightroom marks the item succeeded, failed, or canceled
  only after that handoff completes.
- Concurrent Lightroom tasks share backend resource-vector admission and the
  plugin's `WorkCoordinator` lanes; tasks must not multiply GPU, local-LLM,
  export, Develop/UI, or catalog-write concurrency.
- Hardware detection establishes startup maxima. Runtime memory pressure may
  reduce CPU, GPU batch, and image-byte limits, but never raise them above the
  detected tier or explicit environment overrides.
- Backups, restore, pruning, resets, and policy rebuild/activation are isolated
  by a writer-preferring maintenance barrier in addition to the resource lanes.
  The barrier drains live inference-to-commit workflows and prevents new work
  from slipping in before database replacement. Required pre-prune,
  pre-training-delete, pre-migration, and pre-restore snapshots must persist
  before mutation. Restore validates catalog ownership, checksums, archive
  paths, and SQLite integrity before an atomic swap with rollback.

## 11. Transactional Policy Generations and Absolute Edits

- Build model artifacts under a new inactive generation. Activate only after
  every model, membership, descriptor, coverage row, and artifact succeeds.
- Upload Lightroom training data in bounded chunks, then rebuild once after the
  full transfer. Never refit the complete generation after every chunk.
- Interrupted startup recovery marks only incomplete builds failed; it never
  invalidates the prior active generation.
- Current Lightroom settings are application inputs, never model features.
  Apply strength as `current + strength * (target - current)`. Full strength
  must equal the target exactly and be idempotent.
- Canonically order mixture components before creating deterministic policy
  IDs. Store user custom names independently of model generations.

## 12. LLM Metadata Generation Anti-Patterns

- **Sequential Processing:** Never iterate over items and call `/metadata/generate` sequentially in plugins or scripts. Always batch requests through `/metadata/generate_batch` so the backend can enforce bounded admission and serialize local-model work. Every photo must retain its own image bytes and receive its own vision result; do not clone captions, alt text, titles, or keywords from an embedding-cluster representative.
- **Missing Image Bytes:** Text-only metadata enrichment may use already stored
  per-photo evidence without a new JPEG. A vision prompt, however, must retain
  and use that photo's own bytes; never run vision inference without pixels or
  copy a burst representative's complete keywords, title, caption, or alt text.
  Missing pixels must fail closed or explicitly abstain to the text-only path.

## 12.1 Edit Inference History and Undo Reconciliation

- Every generated recipe has an immutable catalog-local inference row with its
  policy/model provenance, modeled slider keys, pre-edit state, and target.
- Lightroom sends one idempotent application event after attempting the edit.
  Successful global application includes a Develop-settings readback so later
  comparisons use what Lightroom actually stored rather than an assumed target.
- The Help-menu developer action **Reconcile Selected AI Edit State** checks at
  most 100 selected photos and writes their current observed state in one
  Lightroom metadata transaction. Use it after Apply, Undo, Redo, or manual
  slider changes during QA.
- `reverted` and `diverged` are state observations, not user preference labels.
  Do not treat them as rejection/acceptance training evidence.
- Policy resets must not delete edit inference/event history. Removing the
  catalog-local database intentionally removes it.

## 13. Test Discipline and Lightroom Smoke Checks

- **Isolated backend tests:** `server/test/conftest.py` assigns every pytest-xdist worker its own temporary catalog database. Tests must never use an actual catalog path or contact Ollama, LM Studio, or any HTTP service; mock provider and network boundaries explicitly.
- **Required local checks:** Run `uv run pytest test/`, `uv run ruff check src test`, and `python scripts/validate_lrc_plugin.py` before handing off a change.
- **Policy and recommendation changes:** Preserve labelled policy-recovery and candidate-admission fixtures, including expected members, ambiguity abstentions, and rejections. Measure target error, membership precision, and cross-policy leakage.
- **Required Lightroom smoke check:** After changes to Upgrade Assistant, open a real catalog and verify one-style candidate selection, **Show All Candidate Photos**, cancellation, absent/deleted photos, and repeated collection creation. Confirm the Lightroom UI stays responsive and no write transaction yields.

### Rendering-state SDK capability gate

Automatic profile and HDR selection must remain disabled until the current
Lightroom Classic version passes this catalog-local capability spike. The spike
does not train a model, alter StyleAI databases, or change the production edit
path. It accepts virtual copies only and restores their captured rendering state
after each test.

1. In a disposable catalog, make two to eight virtual copies from RAW photos.
   Include compatible copies from the same camera with: Adobe built-in,
   camera-matching, and at least one installed custom camera profile. Include
   both SDR and HDR states. Keep copies from other camera models selected if you
   want to confirm that the harness refuses cross-camera application.
2. Select only those disposable virtual copies in Library.
3. Run **Help → Plug-in Extras → Developer: Test Profile and HDR SDK Support**.
4. Inspect the JSON report written to the Desktop. For every profile class,
   record the exact `CameraProfile`, `CameraProfileRaw`, and `Look` values that
   Lightroom returned. Confirm profile-only, HDR-only, and combined tests show
   `matched=true`, and every test shows `restore_verified=true`.
5. Manually apply one verified profile/HDR combination to a disposable virtual
   copy, then use Lightroom Undo and Redo. After each action, reopen Develop and
   confirm both the UI and `getDevelopSettings()` readback (by rerunning the
   spike with that state represented on a selected copy) agree.
6. Temporarily remove or rename the custom profile and confirm Lightroom rejects
   or substitutes it without leaving a mixed profile/HDR state. Restore the
   profile before continuing normal work.

Lightroom's documented plug-in API does not provide profile enumeration, so a
future selector must use only a catalog-local registry of representations
observed in training photos. Do not infer a profile ID from its display name.
The report deliberately leaves `gate_passed=false`: a developer must classify
the observed built-in/camera-matching/custom specimens and record the manual
Undo/Redo and unavailable-profile outcomes. If custom profile application is
not exact and repeatable, profile selection is recommendation-only. If HDR
application/readback is not exact and repeatable, HDR selection is suggestion-
only.

The Lightroom Classic 15.5 Nikon Z7 custom-profile spike covered four custom
profiles in SDR and HDR: 104/104 profile-only, HDR-only, and combined
applications matched exact readback, with zero restore failures or
cross-category changes. Undo worked; Redo did not survive the two SDK catalog
transactions used for apply and verified restore. Production code therefore
does not promise Redo and never simulates it by rerunning the spike or edit.

Profile and HDR controls default independently to **Suggest**. **Off** preserves
the current state without a proposal. **Auto** is conservative: it is eligible
only for a validated selector trained from a Lightroom-target-independent
embedded RAW preview, a compatible
catalog-observed profile, and an available continuous policy for the exact
target rendering state. Auto eligibility uses stricter cross-fitted, per-class
precision and uncertainty gates than Suggest. Lightroom must confirm exact
profile/HDR readback before StyleAI applies sliders. An unavailable or
substituted profile aborts
the slider edit and triggers one bounded restoration attempt.
The unavailable-profile branch is code- and fixture-verified but remains
unverified against a removed real custom profile, because removing the active
profile would disturb unrelated catalog photos.

## 14. Local Catalog Policy Evaluation

Synthetic policy fixtures prove mathematical invariants, but they do not
measure fidelity on a photographer's actual edits. Run burst-safe held-out
evaluation against a catalog-local training collection with:

```sh
cd server
uv run python scripts/evaluate_catalog_policies.py \
  --db-path "/path/to/catalog/styleai.db"
```

The evaluator builds fold-specific production artifacts entirely in memory. It
does not replace the active generation or alter training examples. Reports are
written beneath `styleai.db/evaluation_reports/` by default and include:

- a deterministic dataset fingerprint and all policy schema versions;
- selective prediction coverage, confidence, entropy, and abstentions;
- normalized and raw RMSE by target and target family;
- white-balance classification accuracy and catastrophic-outlier counts;
- estimator, policy-count, local-corrector, partition, and timing diagnostics.

Reports contain aggregate metrics by default. Use `--include-photo-ids` only
when local debugging requires the IDs of the worst predicted examples. Reports
must not be uploaded automatically or treated as cross-catalog training data.

## 15. Recommendation Calibration

Recommendation thresholds and membership/coverage/quality ranking weights must
be supported by labelled local evidence rather than tuned against one anecdotal
candidate list. A review document uses schema
`policy-recommendation-review-v1` and groups candidates by the recommendation
run in which they were reviewed. Each candidate may be labelled:

- `policy_match`: whether the photo truly belongs to the proposed editing policy;
- `useful`: whether it would be a valuable additional training example;
- `null`: not reviewed, and therefore excluded from that metric.

The complete interchange contract is
`docs/schemas/policy-recommendation-review-v1.schema.json`.
Review files contain local embeddings and selected metadata needed to replay
the production ranker. Keep them catalog-local; neither the plugin nor the
calibration command uploads them.

The Upgrade Assistant creates a compact review snapshot automatically. After
opening a candidate collection, select reviewed photos in Library, reopen the
same policy in the assistant, and choose one label:

- **Helpful Example** means `policy_match=true, useful=true`.
- **Fits, But Redundant** means `policy_match=true, useful=false`.
- **Not This Policy** means `policy_match=false, useful=false`.

Labels are durable evaluation evidence; they do not retrain a policy or change
production thresholds. Export labelled sessions, joining their photo IDs to
the canonical Chroma embeddings, with:

```sh
cd server
uv run python scripts/export_policy_recommendation_reviews.py \
  --db-path "/path/to/catalog/styleai.db" \
  --output "/path/to/local-recommendation-reviews.json"
```

Run calibration with:

```sh
cd server
uv run python scripts/calibrate_policy_recommendations.py \
  "/path/to/local-recommendation-reviews.json"
```

The report measures policy precision/leakage/recall, useful-example precision
and recall, NDCG, and selection rate. Parameter selection is cross-validated by
whole review group, and the precision gate uses the lower bound of a 95% Wilson
interval rather than an optimistic point estimate. Recommended values remain
`evaluation_only`; this command never updates production defaults. Upstream
`source_ambiguous` decisions remain hard gates and cannot be loosened by
downstream ranking calibration.

## 16. Applied Edit Outcome Evaluation

Use **File → Plug-in Extras → Rate Selected AI Edits** on selected AI-edited photos to record
one explicit result: Keep, Modified and Kept, or Reject. The action records
feedback only; it never changes Develop settings. Keep is accepted only when
the modeled Lightroom state still matches the confirmed application. Reopen
the action with a different choice to append a corrected judgment—the history
is never overwritten.

Generate a catalog-local report with:

```sh
cd server
uv run python scripts/evaluate_applied_edits.py \
  --db-path "/path/to/catalog/styleai.db"
```

Reports are written under `styleai.db/evaluation_reports/` and follow
`docs/schemas/applied-edit-quality-v1.schema.json`. They include application
confirmation/failure counts, review coverage, explicit outcome rates,
corrections to delivered targets, confidence calibration, and generation-level
Wilson intervals. Rendering decisions are reported separately from slider
errors, stratified by Suggest and Auto; unchanged suggestions are not counted
as accepted or rejected. HDR activations have a dedicated return-rate report.
Rejected edits are preference evidence but never numeric
regression targets. Results and generation comparisons remain
`evaluation_only`; they do not retrain, activate, or adjust a model.
