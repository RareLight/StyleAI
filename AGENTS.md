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
│       ├── services/              # Logic (chroma, index, search, face, style_engine, style_grouping)
│       └── providers/             # LLM APIs (ollama, lmstudio via auto-discovered ports)
└── .agents/rules/                 # Agent constraint files
```

### Key Storage Locations
- **Databases (`styleai.db`)**: Located in user's Catalog folder (`~/Pictures/Lightroom/styleai.db`). Passed via `--db-path`.
  - `styleai.db/chroma.sqlite3`: ChromaDB vector embeddings.
  - `styleai.db/styles.sqlite`: Relational metadata, styles, and face templates.
- **Model Cache**: SigLIP2, InsightFace, and SentenceTransformer cached in `~/.cache/huggingface/` or `~/.insightface/`.
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
- **Post-discovery work**: Coalesce repeated discovery follow-up jobs. Predictive fitting runs before optional prose summarization; signature summaries are disabled unless `STYLEAI_SUMMARY_MODEL` explicitly selects a local `ollama::<model>` or `lmstudio::<model>`.

---

## 4. ML Architecture & Taxonomy Constraints

### Database Isolation & Image Exports
- **Collection Isolation**: ChromaDB `photos` (Search) and `training_examples` (Style Training) MUST remain strictly isolated.
- **Training Image Pixels**: "Train AI Style" requires JPEG exports for pixel metrics (`zone_deep_shadows`, `histogram_signature`, `dominant_colors`). Missing JPEG bytes during text-only metadata generation must handle gracefully (proceed with text metadata without HTTP 400 errors).

### Style Curation & Tonal Regression
- **Burst Curation**: Cluster photos with capture time $\Delta t \le 10\text{s}$ and SigLIP2 distance $\le 0.05$. Select hero shots by star rating > pick status (`pick_status == 1`) > edit complexity. Weight hero shots by $w_i = 1.0 / |C|$.
- **Supervised Regression**: Use **Partial Least Squares (`WeightedPLSRegression`)** with row scaling ($X \odot \sqrt{w}, Y \odot \sqrt{w}$) for $15 \le N < 50$. Use **Elastic Net (`ElasticNet`)** ($L_1\text{-ratio}=0.2, \alpha=0.1$) for $N \ge 50$. Never use unsupervised PCA.
- **Math Defaults & Clamping**: Default missing targets to linear bounds (1.0 crops, 50.0 color blend, linear point curves). Universally clamp predictions to learned `slider_bounds` and blend recipes with linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$).
- **HDR & Panoramas**: SigLIP2 SDR model uses base SDR JPEG + appends `+ HDR` profile suffix for HDR photos. Panoramas (`-Pano`, `_Pano`, `panorama` tag, aspect ratio $\ge 2.2:1$) are excluded from training and recommendations.
- **WB Threshold**: Categorical WB (`is_custom`) requires a 0.7 probability threshold to override "As Shot". Normalize crops via `avg_dim = (width + height) / 2.0`.

### Genre Taxonomy & Classification Pipeline
Classification uses the multi-tiered pipeline (`style_grouping._primary_genre_with_keywords`) only as an interpretable label and guardrail. NEVER use ad-hoc keyword exception lists or early return short-circuits.
1. **Keywords & Semantic Vectors**: Explicit dictionary keywords take precedence. SentenceTransformer vector mapping (cosine distance $\le 0.45$) overrides vision scene tags ONLY if mapping to a Specialized Subject Regime (astrophotography, macro, event). Broad regimes act as fallbacks.
   - Only keywords explicitly supplied with the training request are authoritative. Never alias AI-generated search-index `keywords` or `flattened_keywords` to `user_keywords`, and never copy generated labels into training records during metadata enrichment.
2. **Vision Scene Tags**: Evaluate top 6 tags (`content_tags[:6]`). For suppressed subjects (`dog`, `pet`, `insect` masked by `nature`/`outdoors`), evaluate up to index 12 (`[:12]`). Map domestic tags (`domestic`, `dog`, `mammal`) to `scene_portrait`.
3. **EXIF Bayesian Priors**: Evaluated via `_evaluate_exif_priors`. `scene_night` (0.40) may independently trigger classification when subject evidence does not conflict. A macro-capable lens is weak corroboration only and MUST NOT classify a photo as macro by itself because macro lenses are routinely used at ordinary focus distances. Other priors (`scene_portrait`, `scene_landscape`, `scene_studio` 0.15–0.20) act as disambiguation signals.
4. **Sensor Crop Factors**: Evaluate focal lengths against 35mm full-frame equivalents via `_get_35mm_equivalent_focal_length` (parsing crop factors for Sony, Canon, Nikon, Fuji, OM System, Leica).
5. **Macro Verification**: Prefer direct visual macro evidence. A known non-macro lens may reject weak macro tags; a `macro`, `micro`, or `mc` lens can corroborate visual evidence but is never sufficient alone.
6. **Embedding-first verification**: Dense SigLIP2 neighborhoods control recommendation admission and visual-cohesion splitting. Text/EXIF disagreement is a stronger-evidence guardrail, not a hard retrieval gate. Split a profile/genre group only into stable components of at least two examples (`split_examples_by_visual_cohesion`); retain sparse or unembedded groups intact. View-time queries trust database `style_id` linkage.
7. **Cache Invalidation & Rule Versioning**: Increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py` when modifying grouping rules to purge `semantic_genre_cache` and set `NEEDS_REDISCOVERY = '1'`. Backend routes MUST invoke `catalog_service._ensure_initialized()` at entry points.

### LLM Batching & GPU Synchronization
- **LLM Batching Protocol**: Lua plugin MUST send batch requests to `/metadata/generate_batch` (never call single `/metadata/generate` sequentially in loops).
- **Dynamic Port & Runner Prefixes**: Auto-discover local LLM hosts (`find_default_local_api_host()`); use explicit runner prefixes (`ollama::`, `lmstudio::`).
- **GPU Pipeline Synchronization**: Downstream LLM workers must pause on `active_embeddings_uuids` gate until upstream vision embedding workers commit output to the database.

### Embedding-first Style Discovery and Recommendations
- **Do not use taxonomy as the primary gate**: Genre labels, tags, and EXIF are probabilistic priors and review aids. They must not be hard-coded exception ladders that decide visual membership before embeddings are evaluated.
- **Component membership**: Within hard camera/profile/HDR partitions, represent a style using dense visual/editing components (medoids and calibrated membership distributions), not a single centroid or a global cosine threshold. Reject candidates that are ambiguous between competing styles.
- **Recommendation selection**: Retrieve visual neighbors through Chroma, then re-rank by component membership, quality, burst deduplication, and coverage of underrepresented components. Maintain labelled regression fixtures and measure precision/leakage before changing membership logic.
- **Cohesion scaling**: Exact full cosine matrices are acceptable only for small groups. Large groups must use deterministic blockwise or bounded-neighbor graph construction so memory does not grow as `N²`.
