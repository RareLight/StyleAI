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
- **API Envelope**: REST on default port `19819`. All responses return `{"results": {...}, "error": null, "warning": null}`.
- **Photo Identity**: Use stable metadata/MD5 `globalPhotoId` (`Util.getGlobalPhotoIdForPhoto`).
- **Multi-Catalog Isolation**: Always filter ChromaDB queries (`collection.get()`) by active `catalog_ids`.

### Lua Plugin Conventions
- **Asynchronicity & Teardown**: Run long operations in `LrTasks.startAsyncTask`. **CRITICAL**: Use `LrTasks.pcall` instead of native `pcall`. Never wrap Lightroom shutdown hooks (`doneFunc`) in native `pcall` (causes C-boundary yield crashes).
- **Yielding & Spin-Locks**: NEVER call `LrTasks.yield()` inside `withWriteAccessDo` closures. On macOS, use `LrTasks.yield(); LrTasks.sleep(0.01)` to prevent C-stack overflows during batching.
- **Batch Transactions**: Consolidate loop updates into a **single** `withWriteAccessDo` block per batch. Never put `withWriteAccessDo` inside a `for` loop.
- **SDK Collection Quirk**: Do NOT call `getChildCollections()` on a newly created `LrCollectionSet` within the same transaction; track sets in memory until committed.
- **SDK UI Quirk**: Avoid `share()` or `width_in_chars` on mixed UI elements (`popup_menu`, `simple_list`). Center elements in a column with explicit pixel width (e.g. `width = 600`).
- **State & Localization**: Wrap UI strings in `LOC()`. Synchronize `en`, `de`, `fr`. Explicitly reset hidden UI binding state on mode switches. Ensure `Util.getPhotoExif` extracts `lens`.

### Python Backend Conventions
- **Architecture**: Endpoints in `routes/`, business logic in `services/`, LLMs in `providers/`. Subpackage imports use relative form (`from .face import ...`), cross-subpackage imports use absolute form (`from services.face import ...`).
- **Memory Optimization**: ALWAYS call `Image.thumbnail()` BEFORE `.convert("RGB")` when processing images to prevent OOM memory spikes.
- **Logging & Errors**: Always use configured `logger` with `exc_info=True`. Surface user errors via standard JSON envelope.

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
Classification MUST use the multi-tiered pipeline (`style_grouping._primary_genre_with_keywords`). NEVER use ad-hoc keyword exception lists or early return short-circuits.
1. **Keywords & Semantic Vectors**: Explicit dictionary keywords take precedence. SentenceTransformer vector mapping (cosine distance $\le 0.45$) overrides vision scene tags ONLY if mapping to a Specialized Subject Regime (astrophotography, macro, event). Broad regimes act as fallbacks.
2. **Vision Scene Tags**: Evaluate top 6 tags (`content_tags[:6]`). For suppressed subjects (`dog`, `pet`, `insect` masked by `nature`/`outdoors`), evaluate up to index 12 (`[:12]`). Map domestic tags (`domestic`, `dog`, `mammal`) to `scene_portrait`.
3. **EXIF Bayesian Priors**: Evaluated via `_evaluate_exif_priors`. `scene_night` (0.40) and `scene_macro` (0.35) can independently trigger classification (floor $\ge 0.30$). Other priors (`scene_portrait`, `scene_landscape`, `scene_studio` 0.15–0.20) act as disambiguation signals.
4. **Sensor Crop Factors**: Evaluate focal lengths against 35mm full-frame equivalents via `_get_35mm_equivalent_focal_length` (parsing crop factors for Sony, Canon, Nikon, Fuji, OM System, Leica).
5. **Strict Macro Verification**: `scene_macro` requires explicit lens string check (`macro`, `micro`, `mc`).
6. **Regime Fallbacks & Verification**: Include ALL primary regimes (including `scene_macro`, `scene_nature`) in `canonical_regimes`. Enforce visual-semantic compatibility via `is_genre_compatible` and `verify_photo_visual_membership` (threshold $\ge 0.60$ for ambiguous categories). View-time queries trust database `style_id` linkage.
7. **Cache Invalidation & Rule Versioning**: Increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py` when modifying grouping rules to purge `semantic_genre_cache` and set `NEEDS_REDISCOVERY = '1'`. Backend routes MUST invoke `catalog_service._ensure_initialized()` at entry points.

### LLM Batching & GPU Synchronization
- **LLM Batching Protocol**: Lua plugin MUST send batch requests to `/metadata/generate_batch` (never call single `/metadata/generate` sequentially in loops).
- **Dynamic Port & Runner Prefixes**: Auto-discover local LLM hosts (`find_default_local_api_host()`); use explicit runner prefixes (`ollama::`, `lmstudio::`).
- **GPU Pipeline Synchronization**: Downstream LLM workers must pause on `active_embeddings_uuids` gate until upstream vision embedding workers commit output to the database.
