# Project Instructions: StyleAI

These instructions apply to the StyleAI repository and override global default AI coding agent instructions.

> [!IMPORTANT]
> **Instructions for AI Coding Agents**:
> - **Update, Don't Overwrite**: When initializing or discovering project details, append/integrate new findings. NEVER delete existing core architectural constraints.
> - **User Action Notifications**: When code changes affect grouping logic, EXIF extraction, DB schema, or ML models, explicitly state required user actions in Lightroom (e.g., clicking "Reset & Discover" or re-running "Train AI Style").

---

## 1. Stack & Directory Structure

**StyleAI** is a local-first, privacy-centric Adobe Lightroom Classic plugin (Lua) and Python background server.

```
StyleAI/
├── plugin/StyleAI.lrdevplugin/   # Lightroom Plugin (Lua UI, Develop settings, task orchestration)
│   ├── Init.lua                   # Entry point and module initialization
│   ├── Util.lua                   # Stable Photo IDs, hashing, EXIF helpers
│   ├── APISearchIndex.lua         # REST API client (Port 19819)
│   ├── DevelopEditManager.lua     # Lightroom Develop settings manager
│   ├── Task*.lua                  # User-facing async tasks (e.g., TaskAnalyzeAndIndex.lua)
│   └── TranslatedStrings_*.txt    # Localization files (en, de, fr)
├── server/                        # Python 3.11+ / Flask Backend (uv managed)
│   ├── pyproject.toml & uv.lock   # Dependencies (managed exclusively via uv)
│   ├── test/                      # Pytest suite (`uv run pytest test/`)
│   ├── scripts/                   # Dev tools (`download_models.py`, `lint_format.sh`)
│   └── src/
│       ├── styleai_server.py      # Entry point (HTTP REST, port 19819)
│       ├── config.py              # Path resolution & configuration
│       ├── server_lifecycle.py    # Process PID & OK file signalling, 30-min idle SigLIP2 unload
│       ├── routes/                # Flask Blueprints (API endpoints)
│       ├── services/              # Search/index logic and editing-policy v2 runtime
│       └── providers/             # LLM APIs (ollama, lmstudio via auto-discovered ports)
└── .agents/rules/                 # Agent constraint files
```

### Key Storage Locations
- **Databases (`styleai.db`)**: Located in user's Catalog folder (`~/Pictures/Lightroom/styleai.db`). Passed via `--db-path`.
  - `styleai.db/chroma.sqlite3`: ChromaDB vector embeddings.
  - `styleai.db/styles.sqlite`: Transactional policy generations, memberships, diagnostics, and custom names.
  - `styleai.db/policy_v2_models/`: Versioned local regression artifacts.
- **Model Cache**: SigLIP2 and InsightFace cached in `~/.cache/huggingface/` or `~/.insightface/`.
- **Logs**: Plugin logs in `~/Documents/LrClassicLogs/`; Backend logs in `<catalog>/styleai.db/` or stdout.

---

## 2. Development Setup & Commands

All Python dependencies are managed exclusively with [uv](https://docs.astral.sh/uv/). Never edit or create `requirements.txt`.

| Task | Command |
| :--- | :--- |
| **Sync Backend Dependencies** | `cd server && uv sync` |
| **Download & Cache ML Models** | `cd server && uv run python scripts/download_models.py` |
| **Add Dependency** | `cd server && uv add <package>` (or `uv add --dev <package>`) |
| **Format & Lint** | `bash server/scripts/lint_format.sh` (runs ruff check and ruff format) |
| **Run Backend Tests** | `cd server && uv run pytest test/` |
| **Evaluate Editing Policies** | `cd server && uv run python scripts/evaluate_editing_policies.py` |
| **Evaluate Local Catalog Policies** | `cd server && uv run python scripts/evaluate_catalog_policies.py --db-path /path/to/styleai.db` |
| **Evaluate Applied Edit Outcomes** | `cd server && uv run python scripts/evaluate_applied_edits.py --db-path /path/to/styleai.db` |
| **Export Recommendation Reviews** | `cd server && uv run python scripts/export_policy_recommendation_reviews.py --db-path /path/to/styleai.db --output /path/to/reviews.json` |
| **Calibrate Recommendations** | `cd server && uv run python scripts/calibrate_policy_recommendations.py /path/to/reviews.json` |
| **Benchmark Policy Scaling** | `cd server && uv run python scripts/benchmark_policy_scaling.py` |
| **Start Backend Server** | `cd server && uv run python src/styleai_server.py` |
| **Plugin Smoke Tests** | Run inside Lightroom via `TaskAutomatedTests.lua` |
| **Sync Translations** | `python sync_translations.py` (Updates `en`, `de`, `fr` files) |

---

## 3. Core Architecture & Conventions

### Communication & Photo Identity
- **Local-only boundary**: REST is fixed to loopback port `19819`. The plugin must not support remote backend URLs, cloud model providers, API keys, or network egress of catalog images/metadata. LLM providers are limited to locally running open-weights models through Ollama or LM Studio.
- **API Envelope**: REST on default port `19819`. All responses return `{"results": {...}, "error": null, "warning": null}`.
- **Photo Identity**: Use stable metadata/MD5 `globalPhotoId` (`Util.getGlobalPhotoIdForPhoto`).
- **Catalog ownership**: A backend process and its `<catalog parent>/styleai.db` belong to exactly one Lightroom catalog. Do not implement `catalog_id`/`catalog_ids`, cross-catalog claims, or shared-database routing. Validate that all stored records retain the active catalog's stable Lightroom UUID/global ID.

### Lua Plugin Conventions
- **Asynchronicity & Teardown**: Run long operations in `LrTasks.startAsyncTask`. **CRITICAL**: Use `LrTasks.pcall` for normal async tasks. However, Lightroom shutdown hooks (`doneFunc` in `LrShutdownFunction`) MUST use native `pcall` because the async scheduler is unreliable during teardown and `LrTasks.pcall` will hang. NEVER use `LrTasks.execute` inside a teardown hook because it yields; use `os.execute` for synchronous/background OS calls instead.
- **Yielding & Spin-Locks**: NEVER call `LrTasks.yield()` inside `withWriteAccessDo` closures. On macOS, use `LrTasks.yield(); LrTasks.sleep(0.01)` to prevent C-stack overflows during batching.
- **Batch Transactions**: Consolidate loop updates into a **single** `withWriteAccessDo` block per batch. Never put `withWriteAccessDo` inside a `for` loop.
- **SDK Collection Quirk**: Do NOT call `getChildCollections()` on a newly created `LrCollectionSet` within the same transaction; track sets in memory until committed.
- **SDK UI Quirk**: Avoid `share()` or `width_in_chars` on mixed UI elements (`popup_menu`, `simple_list`). Center elements in a column with explicit pixel width (e.g. `width = 600`).
- **State & Localization**: Wrap UI strings in `LOC()`. Synchronize `en`, `de`, `fr`. Explicitly reset hidden UI binding state on mode switches. Ensure `Util.getPhotoExif` extracts `lens`.

### Python Backend Conventions
- **Architecture**: Endpoints in `routes/`, business logic in `services/`, LLMs in `providers/`. Subpackage imports use relative form (`from .face import ...`), cross-subpackage imports use absolute form (`from services.face import ...`).
- **Memory Optimization**: ALWAYS call `Image.thumbnail()` BEFORE `.convert("RGB")` when processing images to prevent OOM memory spikes. This applies everywhere, including when generating base64 image strings for LLM payloads.
- **Model Initialization**: Keep vision-model and tokenizer fallback paths independent. A tokenizer lookup failure must not instantiate or load SigLIP2 weights a second time.
- **Logging & Errors**: Always use configured `logger` with `exc_info=True`. Surface user errors via standard JSON envelope.
- **LLM Concurrency & Batching**: NEVER increase `STYLEAI_LLM_CONCURRENCY` above 1 by default. Trying to force parallel local LLM requests (Ollama/LM Studio) forces the GPU to context-switch, immediately maxing out VRAM and deadlocking the process.
- **Bounded pipeline**: The server owns job admission, image-byte budgets, per-item completion state, and cancellation. Lua must not create unbounded producer queues or multiple long-running LLM requests. Metadata generation may begin only after the corresponding embedding state is terminal.
- **Hardware tiers**: Use `config.get_index_resource_limits()` rather than hard-coded queue, GPU batch, or Waitress thread counts. Apple Silicon defaults are bounded by unified memory (16 GB: 8/32/8; 32 GB: 12/48/12; 64 GB+: 16/64/16 for GPU batch/queue/HTTP threads); only explicit `STYLEAI_*` environment overrides may exceed them.
- **Catalog traversal**: Use Chroma `count()` for totals and bounded `limit`/`offset` pages for collection-wide maintenance. Never introduce fixed million-record loads or silent catalog-size ceilings.
- **Shutdown recovery**: Keep Lightroom teardown non-blocking. Persist the catalog session marker before forced backend exit; interrupted sessions must pass SQLite integrity checking and invalidate derived discovery/recommendation state at startup.
- **Policy rebuilds**: Coalesce repeated training mutations and rebuild exactly once after a complete Lightroom training upload, never once per transport chunk. Build and validate a complete inactive generation, atomically replace its artifacts, and activate it only after all relational rows and artifacts succeed. A failed candidate must not retire the prior active generation. Prune inactive derived generations after successful activation.
- **Discovery UX and scaling**: `/styles/discover` must only acknowledge a catalog-local background rebuild; Lightroom polls its status rather than holding an HTTP request through model fitting. Do not use repeated EM refits for a one-policy baseline, and do not run coordinate-descent Elastic Net against the full embedding vector when features greatly outnumber partition examples; preserve broad-policy quality with the stable ridge/PLS candidates instead.

---

## 4. ML Architecture & Taxonomy Constraints

### Database Isolation & Image Exports
- **Collection Isolation**: ChromaDB `image_embeddings` (Search) and `edit_training` (Style Training) MUST remain strictly isolated.
- **Training Image Pixels**: "Train AI Style" requires JPEG exports for pixel metrics (`zone_deep_shadows`, `histogram_signature`, `dominant_colors`). Missing JPEG bytes during text-only metadata generation must handle gracefully (proceed with text metadata without HTTP 400 errors).

### Style Curation & Conditional Regression
- **Burst Curation**: Cluster photos with capture time $\Delta t \le 10\text{s}$ and SigLIP2 distance $\le 0.05$. Select hero shots by star rating > pick status (`pick_status == 1`) > edit complexity. Weight hero shots by $w_i = 1.0 / |C|$.
- **Supervised Regression**: Select the production expert family independently for each compatible partition using burst-grouped held-out validation across reduced-rank ridge, weighted PLS, and multi-task Elastic Net. Use the selected pickle-safe factory consistently for mixture discovery and shrunken hierarchical camera/profile residual calibration. Keep nonlinear challengers in the offline evaluation harness unless held-out evidence and adequate sample size justify production use.
- **Math Defaults & Clamping**: Default missing targets to linear bounds (1.0 crops, 50.0 color blend, linear point curves). Universally clamp predictions to learned `slider_bounds` and blend recipes with linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$).
- **HDR & Panoramas**: Keep HDR state separate from camera-profile identity. New writes must use the versioned rendering-state contract and must never append `+ HDR` to profile names; that suffix is accepted only when reading legacy metadata. SigLIP2 and categorical selectors use Lightroom-target-independent embedded RAW previews when available. Panoramas (`-Pano`, `_Pano`, `panorama` tag, aspect ratio $\ge 2.2:1$) are excluded from training and recommendations.
- **Rendering selector safety**: Suggest and Auto have separate evidence gates. Auto requires burst-group-preserving cross-fitted evaluation, per-class precision, uncertainty bounds, camera compatibility, and an exact continuous artifact for the effective rendering state. Profile Auto must condition on effective HDR, never on an unapplied HDR suggestion. Selector scores are ranking confidence unless calibration is independently demonstrated.
- **WB Threshold**: Categorical WB (`is_custom`) requires a 0.7 probability threshold to override "As Shot". Normalize crops via `avg_dim = (width + height) / 2.0`.

### Taxonomy-Free Policy Discovery
- **No product ontology**: Do not restore genre buckets, semantic genre caches, keyword exception ladders, or subject × lighting style IDs. Policy discovery is driven by edited target behavior and source-space recognizability.
- **Hard partitions only for incompatibilities**: HDR state and normalized camera profile may partition training. Camera make/model/lens are regularized calibration categories, not style identities.
- **Descriptors are explanatory**: User keywords and local visual tags may name or explain a discovered policy after fitting. They never admit training examples or recommendations.
- **Stable identity**: Canonically order mixture components before assigning deterministic policy IDs. Persist user custom names outside model generations so retraining does not discard them.

### LLM Batching & GPU Synchronization
- **LLM Batching Protocol**: Lua plugin MUST send batch requests to `/metadata/generate_batch` (never call single `/metadata/generate` sequentially in loops).
- **Dynamic Port & Runner Prefixes**: Auto-discover local LLM hosts (`find_default_local_api_host()`); use explicit runner prefixes (`ollama::`, `lmstudio::`).
- **GPU Pipeline Synchronization**: Downstream LLM workers must pause on `active_embeddings_uuids` gate until upstream vision embedding workers commit output to the database.

### Embedding-first Style Discovery and Recommendations
- **Do not use taxonomy as the primary gate**: Genre labels, tags, and EXIF are probabilistic priors and review aids. They must not be hard-coded exception ladders that decide visual membership before embeddings are evaluated.
- **Component membership**: Within hard camera/profile/HDR partitions, represent a style using dense visual/editing components (medoids and calibrated membership distributions), not a single centroid or a global cosine threshold. Reject candidates that are ambiguous between competing styles.
- **Policy discovery geometry**: Initialize production policy candidates from grouped out-of-fold target residuals so high-dimensional source features cannot dominate editing-policy identity. Fit source recognizability afterward using normalized cosine geometry; never coordinate-standardize dense image embeddings before membership distance calculations.
- **Recommendation selection**: Retrieve visual neighbors through Chroma, then re-rank by component membership, quality, burst deduplication, and coverage of underrepresented components. Maintain labelled regression fixtures and measure precision/leakage before changing membership logic.

### Editing-Policy v2 Architecture
- **Style identity**: A trained style represents a conditional mapping from unedited source-image evidence to absolute Lightroom develop targets. Subject matter, lighting, location, and open-vocabulary descriptions are features and diagnostics; they are not style IDs or mandatory grouping keys.
- **Genre-neutral evaluation**: New discovery algorithms must pass `scripts/evaluate_editing_policies.py` and its synthetic policy-recovery fixtures. Burst groups must remain within one validation fold. The current user catalog is an evaluation sample, never the source of a fixed product ontology.
- **Absolute targets**: Current Lightroom settings must never influence target inference. At application time, interpolate each modeled value from current to absolute target as `current + strength * (target - current)`. Full-strength application must be idempotent.
- **Model proliferation**: Begin with one broad conditional policy and add experts only when grouped held-out validation, effective support, and stability justify the added complexity. Never create Cartesian subject × lighting style partitions.
- **Open-vocabulary insights**: Generate policy descriptions only after mathematical policy discovery, from descriptors actually observed in local/user-provided evidence, with provenance retained. Coverage buckets must be learned from visual components and empirical feature/category distributions; do not encode a fixed product genre vocabulary.
- **Ambiguity and coverage**: Ambiguous source-space membership must abstain rather than blend competing policies. Coverage gain may rank already-admissible candidates, but it must never override membership precision or turn a weak visual match into a recommendation.
- **Large-scale inference**: Never replace the validated global policy solely because a catalog crosses a fixed example count. A policy-restricted local corrector may learn only grouped out-of-fold residuals, remains enabled only after material held-out improvement, uses at most 100 neighbors within cosine distance 0.15, and must abstain on sparse or high-variance neighborhoods. Local correction is applied before the same learned target clamps; abstention falls back to the unchanged global prediction.
- **Bounded discovery validation**: Repeated estimator and policy-count cross-validation must use a deterministic, burst-group-preserving bounded sample on large partitions. Local residual validation and its artifact bank must also remain bounded. Final global policy/calibration fitting still uses every curated example.
- **Recommendation order**: Retrieve bounded multi-medoid neighborhoods in one batched Chroma query, exclude existing examples and hard-partition mismatches, apply calibrated membership/entropy admission, deduplicate bursts, then rank by membership, coverage gain, and user quality signals. Never blend competing policy targets or assign one candidate globally before its policy membership is known.
- **Recommendation feedback**: Persist catalog-local review snapshots with generation, policy, and schema provenance. User labels are evaluation evidence and must never mutate active thresholds or models automatically. Keep embeddings canonical in Chroma and materialize them only for an explicit local export.
- **Cohesion scaling**: Exact full cosine matrices are acceptable only for small groups. Large groups must use deterministic blockwise or bounded-neighbor graph construction so memory does not grow as `N²`.
- **Edit inference history**: Persist every returned recipe as an immutable
  inference with generation/policy/schema provenance plus canonical pre-edit
  and absolute-target fingerprints. Append idempotent Lightroom application
  and reconciliation events; never overwrite history or erase it during a
  derived policy reset.
- **Undo reconciliation**: Compare only the inference's modeled sliders against
  Lightroom readback. Record `reverted` or `diverged` as observed state, never
  as inferred user approval/rejection. Keep selected/recent reconciliation
  bounded to at most 100 photos per request and consolidate Lightroom metadata
  updates into one private write transaction.
- **Explicit edit outcomes**: `accepted`, `rejected`, and
  `modified_and_kept` must come from an explicit Lightroom action. Capture a
  modeled-slider readback with each judgment. Rejections are preference labels,
  not regression targets; only accepted or modified-and-kept final states may
  contribute numeric correction metrics.
- **Outcome calibration**: Applied-edit quality and confidence reports remain
  `evaluation_only`. Require adequate per-generation review counts and
  uncertainty intervals before recommending a challenger; never change active
  models or thresholds automatically from user outcomes.
