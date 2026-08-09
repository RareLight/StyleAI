# Developer Guide

This guide describes the current StyleAI frontend/backend contract. Repository
rules in [`AGENTS.md`](../../AGENTS.md) are authoritative.

## Setup and packages

StyleAI requires Python 3.12+ and uses `uv` exclusively:

```sh
bash scripts/setup-local-uv-env.sh
cd server
uv run python scripts/download_models.py
```

The checked-in `plugin/StyleAI.lrdevplugin` is the release source and registers
only the six production workflows. Generate disposable packages without
changing that manifest:

```sh
python scripts/package_lrc_plugin.py developer
python scripts/package_lrc_plugin.py release
```

The developer package adds automated tests, benchmarks, edit reconciliation,
and rendering-state capability checks to Lightroom's Help menu.

On macOS, `scripts/styleai-installer.sh redeploy` is a source-development tool.
Lightroom must be stopped. The command stops the recognized StyleAI process on
port 19819, stages and verifies a complete `StyleAI.lrdevplugin` copy, then
atomically replaces the Modules copy. On next launch, that development plug-in
resolves the current checkout's `server/src/styleai_server.py` and starts it
through `uv`. A packaged release instead launches its bundled backend binary.

## Component boundaries

- Lua owns Lightroom selection, proxy export, progress, dialogs, catalog
  metadata, collections, virtual copies, and Develop application.
- `server/src/routes` owns request validation and response serialization.
- `server/src/services` owns durable jobs, admission, image processing,
  persistence, training, policy inference, recommendations, and evaluation.
- `server/src/providers` contains only local Ollama and LM Studio metadata
  adapters. Learned editing is LLM-free.

REST is fixed to `127.0.0.1:19819`. Responses use:

```json
{"results": {}, "error": null, "warning": null}
```

New endpoints must preserve this envelope and catalog ownership checks. The
main API groups are `/initialize`, `/operations`, `/index*`,
`/metadata/generate_batch`, `/training*`, `/styles*`, `/style_edit*`, `/db*`,
and service health/lifecycle routes.

## Catalog identity and storage

The active catalog's parent directory contains `styleai.db`; keep each catalog
in its own directory. One backend process may bind to only one database path.
The database contains a generated `catalog_database_id` used to reject backup
marker mismatches; it is not Lightroom's catalog UUID. Stable global photo IDs
link Lightroom photos to backend records.

```text
styleai.db/
  chroma.sqlite3                  Chroma metadata/index database
  <Chroma segment files>          image_embeddings and edit_training vectors
  styles.sqlite                   jobs, policies, reviews, history, ownership
  policy_v2_models/<generation>/  immutable joblib artifacts
  evaluation_reports/             local evaluation output when requested
```

Never join the two Chroma collections implicitly or treat their counts as
interchangeable. Traverse large collections with `count()` and bounded pages.

SQLite schema changes use `server/src/migrations/versions/00X_*.py`. Define
`upgrade(conn)` and keep migrations forward-only and idempotent where practical.
Packaged builds must include this directory; missing migration modules make a
clean frozen database unusable.

## Lightroom concurrency and teardown

- Capture target photos before opening a modal dialog.
- Run long work in `LrTasks.startAsyncTask` and use `LrTasks.pcall` for normal
  asynchronous error boundaries.
- Never yield inside `withWriteAccessDo` or `withPrivateWriteAccessDo`. Perform
  one catalog write transaction per batch.
- During long loops outside transactions, macOS needs
  `LrTasks.yield(); LrTasks.sleep(0.01)`.
- `WorkCoordinator.lua` bounds Lightroom export, backend request, catalog-write,
  and Develop/UI lanes across simultaneous user workflows.
- Do not query a newly created collection set in its creation transaction;
  track it in memory until commit.

`ShutdownApp.lua` deliberately calls synchronous `doneFunc` with native
`pcall` and does nothing else. Lightroom teardown must never wait for HTTP,
logging, filesystem, tasks, or process launch. The backend unloads idle
SigLIP2 weights after 10 minutes and exits after 10 request-idle minutes when no
live operation/admission/index work exists. Explicit developer redeploy uses
`scripts/server.sh stop`, which cancels, requests shutdown, verifies the port,
and escalates only against a recognized StyleAI PID.

## Durable operations and resource admission

Indexing, metadata, training/discovery, recommendations, and editing create
catalog-local operation jobs. Each photo has its own state. A backend result
that still needs a Lightroom metadata or Develop handoff remains nonterminal;
Lightroom records the final success, failure, or cancellation.

Acquire all required resources atomically through
`services.operations.admission`. Accelerator and local-LLM capacity are
process-wide, so starting several Lightroom tasks does not multiply model
concurrency. Indexing and tagging requests queue; local LLM concurrency is one
by default. Catalog writes are short critical sections. Maintenance uses a
writer-preferring barrier that drains inference-to-commit work before backup,
restore, prune, reset, migration, or policy activation.

Hardware detection sets startup maxima. Apple Silicon limits account for
unified memory; the runtime pressure governor can lower CPU, GPU batch,
in-flight image bytes, and queue use under pressure. Explicit `STYLEAI_*`
environment values are advanced overrides, not normal product settings.

For Pillow inputs, call `thumbnail()` before `convert("RGB")` to avoid decoding
a full-resolution RGB allocation. Keep tokenizer fallback separate from vision
weight loading so model initialization cannot happen twice.

## Prepare Photos and local metadata

Visual analysis creates SigLIP2 embeddings in `image_embeddings`. If analysis
and metadata are requested together, the photo's embedding must commit before
its metadata phase begins.

Lua sends metadata through `/metadata/generate_batch`. Every accepted item
retains its own image bytes until its own vision inference finishes. Burst or
similarity information may improve scheduling, but complete keywords, title,
caption, and alt text must never be cloned between photos. Missing pixels must
fail closed or use an explicitly text-only operation.

Ollama uses loopback port 11434. LM Studio starts from loopback port 1234 and
may be resolved through the SDK's local dynamic-port discovery. Remote/LAN
hosts are rejected. Debug image capture requires both Debug and Capture inputs,
is created lazily, and is bounded by group count and bytes.

## Editing-policy training

`Learn From My Edits` accepts RAW/DNG photos, extracts target-independent source
evidence plus absolute Develop targets, and excludes panoramas. It uploads
bounded chunks and performs one rebuild only after the complete upload.

Within compatible HDR/profile partitions, burst groups preserve validation
boundaries. Production chooses among the stable baseline, reduced-rank ridge,
weighted PLS, and eligible multi-task Elastic Net. Policy identity begins with
grouped out-of-fold target residual behavior; normalized source embeddings then
establish whether a component is recognizable. Genre, lighting, keywords,
camera, and lens do not define styles.

Experts and policy-local residual correction are retained only after material
held-out improvement with adequate support and stable selective coverage. Large
validation searches use bounded deterministic burst-safe samples; the selected
global model still fits all curated examples.

Build a full inactive generation, validate relational rows and artifacts, and
activate it atomically. A failed build leaves the previous generation active.
Custom names, recommendation feedback, and edit history are generation-
independent.

## Rendering state and edit application

HDR and camera profile are separate categorical decisions. Their selectors use
target-independent embedded RAW previews and catalog-observed, camera-compatible
profiles. **Suggest** and **Auto** use different gates; Auto requires stricter
burst-preserving precision/uncertainty evidence, an exact continuous artifact
for the effective rendering state, and exact Lightroom readback. Profile
selection conditions on effective HDR.

Auto application writes HDR/profile first, reads it back, and aborts slider
application on substitution or mismatch. The Lightroom 15.5 Nikon Z7 capability
study verified custom profile/HDR application and restoration; Undo worked,
while Redo did not survive the SDK transaction sequence. Do not promise or
simulate Redo. Use the developer capability spike on disposable virtual copies
when validating a new Lightroom version, camera/profile family, or unavailable-
profile behavior.

The policy predicts absolute targets. Application uses
`current + strength * (target - current)`, so full strength equals the learned
target and repeated full-strength application is idempotent. Clamp to supported
Lightroom and learned bounds. Ambiguous membership, missing partitions, or
insufficient target-independent evidence abstains instead of falling back to an
LLM or unrelated policy.

## Recommendations and feedback

Recommendation generation runs as a cancellable background operation. One
batched Chroma query retrieves bounded neighborhoods around policy medoids.
Hard incompatibilities, existing examples, panoramas, rejected photos,
near-duplicates, and ambiguous matches are removed before burst deduplication.
Coverage, diversity, rating, and pick status rank only admitted members.

The Lightroom assistant stores a bounded review snapshot. **Helpful Example**,
**Fits, But Redundant**, and **Not This Policy** labels are durable evaluation
evidence; they never change the active model or thresholds automatically.

## Edit history, Undo, and explicit outcomes

Before returning a recipe, the backend stores an immutable inference containing
generation/policy/schema provenance, modeled keys, pre-edit fingerprint, and
absolute target. Lightroom appends idempotent application and readback events.

Reconciliation compares only modeled sliders and records observable
`reverted`/`diverged` states. It does not infer why a state changed or convert
Undo into a quality label. **Rate Selected AI Edits** explicitly appends
`accepted`, `rejected`, or `modified_and_kept` with a fresh readback. Rejections
are preference evidence, never numeric regression targets.

## Quality and performance evaluation

```sh
cd server
uv run python scripts/evaluate_editing_policies.py
uv run python scripts/benchmark_policy_scaling.py
uv run python scripts/evaluate_catalog_policies.py --db-path /path/to/styleai.db
uv run python scripts/evaluate_applied_edits.py --db-path /path/to/styleai.db
uv run python scripts/export_policy_recommendation_reviews.py \
  --db-path /path/to/styleai.db --output /path/to/reviews.json
uv run python scripts/calibrate_policy_recommendations.py /path/to/reviews.json
```

Synthetic fixtures validate mathematical recovery and invariants. Catalog
evaluation performs burst-safe held-out fitting in memory and does not replace
the active generation. Recommendation and outcome calibration reports are
versioned, local, and evaluation-only.

Before handoff, run:

```sh
bash server/scripts/lint_format.sh
cd server && uv run pytest test/
cd .. && python scripts/validate_lrc_plugin.py
```

Tests must use isolated temporary databases and mocked network/provider
boundaries. For UI/catalog changes, build the developer plug-in, run its
Lightroom tests, and follow `docs/UI_HUMAN_TEST_MATRIX.md` in a disposable
catalog.

## Release and documentation discipline

The release workflow syncs the locked `uv` environment, freezes the backend
with PyInstaller, includes OpenCLIP package data and migration modules, and
packages it beside the Lightroom plug-in. A clean-database frozen-backend smoke
test is required when dependency, migration, spec, or launch behavior changes.

Wiki sources live in `docs/wiki`. `Project-README.md` is generated from the root
README; run `bash scripts/build-wiki-pages.sh` after changing it. Keep all five
translation files synchronized and report any required Lightroom action after
changing data, features, or learned-model contracts.
