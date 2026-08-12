# Project Instructions: StyleAI

These rules override general agent defaults for this repository. Inspect the
relevant Lua, Python, tests, configuration, and documentation before changing
behavior. Preserve these architectural constraints when updating this file.

> [!IMPORTANT]
> When a change affects source evidence, grouping, EXIF extraction, database
> schema, or a learned model, tell the user exactly what to rerun in Lightroom:
> **Prepare Photos**, **Learn From My Edits**, or **Styles & Training → Rebuild**.

## Product boundary

StyleAI is a local-first Lightroom Classic plug-in with a Lua frontend and a
Python 3.12+ Flask/Waitress service.

- Bind REST only to `127.0.0.1:19819`. Do not add remote service URLs, cloud AI
  providers, API keys, telemetry, or network egress for photos or metadata.
- Generative metadata uses only open-weights models running locally through
  Ollama or LM Studio. Learned editing never depends on an LLM.
- One active Lightroom catalog owns one adjacent `styleai.db`. Because the path
  is `<catalog directory>/styleai.db`, each catalog must live in its own folder.
  A service process may bind to only that path, and backup database-marker
  mismatches must fail closed.
- Do not add cross-catalog routing, shared databases, `catalog_id` collections,
  or migration paths for removed cloud/multi-catalog features.

## Repository map

```text
plugin/StyleAI.lrdevplugin/   Lightroom SDK UI, catalog and Develop operations
  Info.lua                    Six production File > Plug-in Extras workflows
  APISearchIndex.lua          Loopback API client and service launch
  WorkCoordinator.lua        Lightroom-side resource lanes
  Task*.lua / AiEditAction.lua
server/                       uv-managed Python backend
  src/routes/                 HTTP boundary only
  src/services/               indexing, jobs, training, policies, history
  src/providers/              Ollama and LM Studio adapters only
  src/migrations/versions/    ordered styles.sqlite migrations
  test/                       isolated pytest suite
docs/wiki/                    published user and developer documentation
docs/schemas/                 evaluation interchange contracts
```

Catalog-local storage:

- `styleai.db/chroma.sqlite3`: isolated `image_embeddings` and `edit_training`
  Chroma collections.
- `styleai.db/styles.sqlite`: ownership, operation jobs, policy generations,
  recommendations, immutable inference history, and append-only events.
- `styleai.db/policy_v2_models/<generation>/`: versioned local artifacts.
- Plugin logs: `~/Documents/LrClassicLogs/`; service log: beside `styleai.db`.
- SigLIP2 cache: the user's Hugging Face cache.

## Commands and validation

Dependencies are managed exclusively by `server/pyproject.toml` and
`server/uv.lock`. Never create or edit `requirements.txt`.

```sh
bash scripts/setup-local-uv-env.sh
bash server/scripts/lint_format.sh
(cd server && uv run pytest test/)
python scripts/validate_lrc_plugin.py
python sync_translations.py
```

Additional ML checks:

```sh
cd server
uv run python scripts/evaluate_editing_policies.py
uv run python scripts/benchmark_policy_scaling.py
uv run python scripts/evaluate_catalog_policies.py --db-path /path/to/styleai.db
uv run python scripts/evaluate_applied_edits.py --db-path /path/to/styleai.db
```

Package a disposable Lightroom build with
`python scripts/package_lrc_plugin.py developer`; never enable developer menus
in the checked-in release manifest. Keep English, Catalan, German, Spanish, and
French resources synchronized.

## Lightroom frontend rules

- Run long work in `LrTasks.startAsyncTask` and use `LrTasks.pcall` for normal
  asynchronous error boundaries.
- Do not register `LrShutdownApp`. Lightroom 15.5 can deadlock while dispatching
  even an otherwise empty shutdown callback's `doneFunc`. The service owns idle
  shutdown and interrupted-job recovery, so application exit needs no plug-in
  teardown hook.
- Never yield inside `withWriteAccessDo` or `withPrivateWriteAccessDo`. Batch
  mutations into one write transaction. Outside transactions, use
  `LrTasks.yield(); LrTasks.sleep(0.01)` during long macOS loops.
- Do not query `getChildCollections()` on a collection set created in the same
  transaction. Track new sets in memory until commit.
- Capture selected photos before modal dialogs. Resolve work through
  `PhotoSelector` and coordinate export, request, catalog-write, and Develop/UI
  lanes through `WorkCoordinator`.
- Wrap every visible string in `LOC()`. Reset hidden bound state when modes
  change. Avoid `share()`/`width_in_chars` across mixed controls; use a tested
  explicit width only for documented SDK layout exceptions.
- Use `Util.getGlobalPhotoIdForPhoto` for identity and `Util.getPhotoExif` for
  standardized EXIF, including lens and rendering-state evidence.

## Backend, jobs, and data integrity

- Routes validate/serialize requests; services own business logic; providers
  own local LLM adapters. Use relative imports within a subpackage and absolute
  imports across subpackages.
- Return the standard `results`/`error`/`warning` envelope. Log exceptions with
  the configured logger and `exc_info=True`; do not leak sensitive inputs at
  INFO level.
- Call `Image.thumbnail()` before `.convert("RGB")`. Keep tokenizer and vision
  model fallback initialization independent so failures cannot load SigLIP2
  twice.
- Indexing, metadata, training/discovery, recommendations, and editing use
  catalog-local operation jobs with per-photo states and scoped cancellation.
  Backend completion remains nonterminal until required Lightroom handoff is
  recorded.
- Acquire complete resource vectors atomically through
  `services.operations.admission`. Accelerator and local-LLM lanes are
  process-wide. Default `STYLEAI_LLM_CONCURRENCY` remains `1`.
- Use `config.get_index_resource_limits()` and the runtime pressure governor.
  Hardware tiers are maxima; pressure may reduce CPU, GPU batch, queue, HTTP,
  and image-byte limits but never exceed detected or explicit bounds.
- Use Chroma `count()` plus bounded `limit`/`offset` traversal. Avoid fixed
  catalog ceilings, unbounded queues, and full `N x N` similarity matrices for
  large groups.
- Keep `image_embeddings` and `edit_training` strictly isolated. Metadata may
  start only after its photo's embedding commit when both were requested.
- `image_embeddings` stores the canonical target-independent SigLIP vector:
  prefer the embedded RAW preview, retain the rendered Lightroom proxy only for
  local-LLM metadata, and stamp every vector with source fingerprint,
  provenance, model, preprocessing, and schema versions. Editing may reuse a
  vector and its source metrics only when the entire stamp matches; otherwise
  recompute and atomically replace the derived record. Never infer compatibility
  from photo ID or vector presence alone.
- Every vision-metadata item retains and infers from its own pixels. Never copy
  a burst representative's caption, title, alt text, or complete keyword set.
  Send batches to `/metadata/generate_batch`; missing pixels must fail closed or
  take an explicit text-only path.
- Backups, restore, prune, destructive reset, schema migration, policy
  activation, and training deletion share maintenance/catalog-write admission.
  Persist required pre-mutation backups and validate ownership, checksums,
  archive paths, and SQLite integrity before atomic replacement.
- Lifecycle marker cleanup must verify PID/process-token ownership. Startup
  recovery checks interrupted databases/jobs and invalidates only derived state
  that cannot be trusted. Lightroom exit itself stays nonblocking.

## Learned editing architecture

- A style is a conditional mapping from target-independent source evidence to
  absolute Lightroom Develop targets. Current sliders are never model features.
  Apply `current + strength * (target - current)`; full strength is idempotent.
- Training accepts RAW/DNG evidence and excludes panoramas. Burst grouping uses
  capture delta `<=10 s` and SigLIP2 cosine distance `<=0.05`; select heroes by
  rating, pick status, then edit complexity, with weight `1 / burst_size`.
- Do not restore genre buckets, keyword exception ladders, fixed scene probes,
  subject-by-lighting IDs, or any product ontology. Descriptors explain a
  discovered policy but never admit members.
- Discover policy identity from grouped out-of-fold target residual behavior,
  then validate source recognizability with normalized cosine multi-medoid
  geometry. Never coordinate-standardize dense embeddings for membership.
- Hard-partition only true rendering incompatibilities (effective HDR and
  normalized profile). Camera/lens are regularized calibration evidence, not
  styles. Store HDR separately; accept legacy `+ HDR` profile names only while
  reading.
- Select reduced-rank ridge, weighted PLS, or eligible multi-task Elastic Net
  per partition using burst-preserving held-out validation. Keep nonlinear
  challengers offline unless adequate held-out evidence justifies production.
- Start broad and add experts/local residual correction only for material,
  stable held-out improvement. A local corrector uses at most 100 neighbors
  within cosine distance `0.15` and abstains on sparse/high-variance evidence.
- Clamp every prediction to Lightroom-safe and learned target bounds. Missing
  targets use linear defaults (crop `1.0`, color blend `50.0`, linear curves);
  categorical white balance requires probability `>=0.7`.
- Profile/HDR Suggest and Auto have separate gates. Auto requires
  burst-preserving cross-fitted precision/uncertainty, camera compatibility,
  target-independent RAW-preview evidence, exact Lightroom readback, and a
  continuous artifact for the effective rendering state. Profile conditions on
  effective HDR, never an unapplied suggestion.
- Build a complete inactive generation, validate all rows/artifacts, then
  activate atomically. A failed candidate must leave the prior active
  generation intact. Rebuild once after a complete training upload, not per
  chunk; prune inactive derived generations only after activation.
- Canonically order components for deterministic policy IDs. Keep custom names,
  recommendation reviews, and edit history outside disposable generations.

## Recommendations and evaluation

- Retrieve bounded, batched multi-medoid Chroma neighborhoods; exclude existing
  examples, panoramas, incompatible partitions, rejected photos, and ambiguous
  membership before burst deduplication.
- Membership precision is the admission gate. Coverage gain, diversity, rating,
  and pick status rank only already-admissible candidates and never blend
  competing policies.
- Persist recommendation review snapshots with generation/policy/schema
  provenance. Labels and calibration reports are evaluation-only and never
  mutate active models or thresholds automatically.
- Persist every returned edit recipe as an immutable inference with generation,
  policy, schema, pre-edit fingerprint, modeled keys, and absolute target.
  Append idempotent application, reconciliation, and explicit-outcome events.
- Reconciliation compares only modeled sliders. `reverted`/`diverged` are
  observations, not preferences. `accepted`, `rejected`, and
  `modified_and_kept` require an explicit Lightroom action and fresh readback;
  rejected edits are not numeric regression targets.
- Changes to policies or recommendations must preserve synthetic recovery and
  labelled admission fixtures and report target error, selective coverage,
  membership precision, ambiguity abstention, and cross-policy leakage.
