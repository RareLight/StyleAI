# Project instructions

Applies to this repository. These instructions override the IDE-global defaults found in `~/.config/opencode/AGENTS.md` / `~/.gemini/GEMINI.md` when they conflict.

> [!IMPORTANT]
> **Instructions for Future AI Coding Agents**:
> This file is a living document and a starting point for project development.
> - **Update, Don't Overwrite**: When initializing or discovering project details (e.g., via `/init` or context exploration), append/integrate your discoveries into this file. **NEVER** overwrite, replace, or delete existing contents.
> - **Evolve Project Context**: Update the project structure, stack descriptions, conventions, commands, and boundaries as the codebase evolves, but always preserve the core skill rules, workflows, and overrides.

## Project

**StyleAI** — A local-first, privacy-centric Adobe Lightroom Classic plugin and Python background server.

## Project Structure

```
plugin/       → Lightroom Classic plugin source (Lua)
server/       → Python/Flask background backend server
docs/         → Wiki pages and developer documentation
.agents/      → Core agent rules and instructions
```

---

# StyleAI Project Instructions

This section contains the canonical rules, conventions, architecture details, and commands for the StyleAI codebase.

## 1. Project Overview & Tech Stack

**StyleAI** is a local-first, privacy-centric Adobe Lightroom Classic plugin that integrates AI-powered tagging, description, semantic search, culling, face recognition, and develop edits into photography workflows.

- **Lightroom Plugin (Lua)**: Frontend UI using the Adobe Lightroom SDK. Handles task orchestration, metadata management, and applying develop settings.
- **Backend Server (Python/Flask)**: Local background server executing AI model inference, vector database storage, SQLite metadata management, and LLM integrations.
- **ChromaDB**: Vector store for image embeddings (SigLIP2) and face embeddings (InsightFace).
- **SQLite**: Structured database for metadata, face templates, and style training profiles.
- **Supported LLMs**: Ollama, LM-Studio.

## 2. Directory Structure

```
StyleAI/
├── plugin/StyleAI.lrdevplugin/   # Lightroom plugin source (Lua)
│   ├── Init.lua                   # Entry point, module setup
│   ├── Util.lua                   # Photo IDs and general utilities
│   ├── APISearchIndex.lua         # Backend HTTP API client
│   ├── DevelopEditManager.lua     # Lightroom develop settings logic
│   └── Task*.lua                  # User-facing action tasks (e.g., TaskAnalyzeAndIndex.lua)
├── server/                        # Python/Flask backend source
│   ├── pyproject.toml & uv.lock   # Dependency definitions (managed via uv)
│   ├── Dockerfile                 # Container setup for server
│   ├── test/                      # Pytest test suite for backend logic
│   ├── scripts/                   # Development and maintenance utilities
│   └── src/
│       ├── styleai_server.py      # Server entry point
│       ├── config.py              # Configuration and path resolution
│       ├── server_lifecycle.py    # Process PID & OK file signalling, idle unloading
│       ├── routes/                # Flask Blueprints (HTTP endpoints)
│       ├── services/              # Business logic (chroma, index, search, face, style_engine)
│       └── providers/             # LLM provider implementations (ollama, lmstudio)
├── docs/wiki/                     # GitHub Wiki source pages (auto-published)
└── .agents/rules/                 # Always-on constraint files for agents
```

## 2.5 Key Locations & Data Stores

To aid in troubleshooting, here is exactly where the application stores data and models:
- **Databases (`styleai.db`)**: The default location for the databases is inside the user's Lightroom Catalog folder (e.g. `~/Pictures/Lightroom/styleai.db`). 
  - `styleai.db/chroma.sqlite3`: The ChromaDB vector embeddings.
  - `styleai.db/styles.sqlite`: The structured relational database for styles and face templates.
  - The backend receives this path via the `--db-path` argument launched by the plugin.
- **Downloaded Models**: SigLIP2, InsightFace, and SentenceTransformer models are cached in `~/.cache/huggingface/` or `~/.insightface/` on macOS.
- **Log Files**: 
  - **Lua Plugin Logs**: Found in `~/Documents/LrClassicLogs/` (or configured via Lightroom).
  - **Python Backend Logs**: Found inside the `<catalogParent>/styleai.db/` folder alongside the database, or output directly to stdout/stderr.
- **Python Utilities & Scripts**: All backend management scripts are located in `server/scripts/`. Examples: `server/scripts/download_models.py`, `server/scripts/lint_format.sh`.
- **Translations**: The plugin translation files are in `plugin/StyleAI.lrdevplugin/TranslatedStrings_*.txt`. Synchronize them using the `sync_translations.py` script at the root.
- **Tests**: 
  - **Backend**: Python tests are in `server/test/` (run via `uv run pytest test/`).
  - **Frontend/Plugin**: Smoke tests are run inside Lightroom via `TaskAutomatedTests.lua`.

## 3. Development Setup & Commands

### Backend Setup (Python)
Dependencies are managed exclusively by [uv](https://docs.astral.sh/uv/). Do not edit or create `requirements.txt`.
- **Sync Dependencies**: `cd server && uv sync`
- **Cache Models**: `cd server && uv run python scripts/download_models.py` (Downloads models locally)
- **Add Dependency**: `cd server && uv add <package>` (or `uv add --dev <package>`)
- **Format & Lint**: `bash server/scripts/lint_format.sh` (runs ruff check and ruff format)
- **Run Tests**: `cd server && uv run pytest test/`
- **Start Server**: `cd server && uv run python src/styleai_server.py`

### Plugin & Translations (Lua)
- **Smoke Tests**: Run inside Lightroom via `TaskAutomatedTests.lua`.
- **Sync Translations**: `python sync_translations.py` (Must update all three: `TranslatedStrings_en.txt`, `TranslatedStrings_de.txt`, `TranslatedStrings_fr.txt`).

## 4. Architecture & Key Systems

### Communication Protocol
- Communicates via **HTTP REST** on default port `19819`.
- All responses use a standard envelope: `{"results": {...}, "error": null, "warning": null}`.

### Lifecycle Management
- `server_lifecycle.py` controls startup signalling via `styleai-server.OK` and `.pid` files.
- The SigLIP2 model loads lazily on first query and unloads after 30 minutes of inactivity.

### Photo Identity & Catalogs
- Stable metadata-based identity (`globalPhotoId` via `Util.getGlobalPhotoIdForPhoto`) is computed from exposure metadata or partial file MD5 hashes.
- Multi-catalog isolation uses soft-state catalog scoping (`catalog_ids` list); photos are not physically deleted when removed from a single catalog.

## 5. Development Conventions & Rules

### Lua Plugin Conventions
- **Asynchronicity**: Long-running operations must run in `LrTasks.startAsyncTask`.
- **Yielding pcall**: Use `LrTasks.pcall` instead of native `pcall` to allow yielding.
- **Top-Level Actions**: File naming must follow the `Task*.lua` pattern.
- **Localization**: Wrap user strings in `LOC()`. Synchronize updates to `en`, `de`, and `fr` translation files.
- **Error UI**: Surface all errors in Lightroom using `ErrorHandler.handleError`.
- **Platform Branching**: Use globally defined booleans `WIN_ENV` and `MAC_ENV` for OS-specific logic.

### Python Backend Conventions
- **Layering**: Endpoints in `routes/` (Blueprints), core business logic in `services/`, and LLM APIs in `providers/`.
- **Logging**: Always use the configured `logger` and include `exc_info=True` for exceptions.
- **Response Format**: Unconditionally return the standard JSON results/error/warning envelope.
- **Imports**: Sibling-relative form within a subpackage (e.g. `from .face import ...` in `services/`); absolute form across subpackages (e.g. `from services.face import ...` in `routes/`).
- **Infrastructure**: Update `Dockerfile`, `docker-compose-dev.yml`, and `docker-compose-prod.yml` when changing dependencies or environment requirements.

### ML Architecture Constraints (CRITICAL)
- **Database Isolation**: The `photos` collection (Semantic Search) and `training_examples` collection (Style Training) in ChromaDB MUST remain strictly isolated. Do NOT merge them. This separation ensures users can safely prune or wipe their massive search index without risking their precious, manually-curated ML training data.
- **Training Optimization Limits**: During "Train AI Style", the plugin MUST export a JPEG preview and send it to the backend even if the photo was already indexed in the search database. The backend ML engine requires the raw image pixels to compute specialized exposure metrics (`zone_deep_shadows`, `histogram_signature`, `dominant_colors`), which the search database does not calculate or store.
- **HDR Handling**: 
  - The ML Predictive Pipeline (SigLIP2) is an SDR vision model. It does NOT use or accept HDR bracketed JPEGs. It relies purely on the base SDR JPEG to categorize the scene lighting.
  - To prevent HDR edits from corrupting SDR style predictions, the `+ HDR` suffix is automatically appended to the camera profile name for HDR photos.
  - HDR Brackets (`-2EV`, `+2EV`) are used EXCLUSIVELY by the Generative LLM fallback pipeline.
- **White Balance**: Categorical WB ("As Shot" vs "Custom") is predicted as a scalar probability (`is_custom`). During recipe reconstruction, the engine enforces a strict threshold (0.7) to favor "As Shot" unless the AI is highly confident (70%+) that the user would apply a custom WB override in that specific lighting scenario.
- **Crop Handling**: The ML engine predicts cropping by normalizing the aspect ratio (`width = height`) by averaging (`avg_dim = (width + height) / 2.0`) to prevent predicting distorted crops without discarding crop bounding boxes.
- **Unified 3-Pillar Training Curation & Regression**:
  - **Pillar 1 (Burst Curation & Weighting)**: During style training, photos with capture times $\Delta t \le 10\text{s}$ and SigLIP2 cosine distance $\le 0.05$ are automatically clustered into bursts. Hero shots are selected based on highest relative star rating within the cluster, breaking ties with Pick Flags (`pick_status == 1`) and edit complexity. Surviving hero shots share normalized cluster density weight ($w_i = 1.0 / |C|$).
  - **Pillar 2 (Small Datasets, $15 \le N < 50$)**: Uses supervised **Partial Least Squares (`WeightedPLSRegression`)** instead of unsupervised PCA. Row scaling ($X \odot \sqrt{w}, Y \odot \sqrt{w}$) is applied prior to NIPALS decomposition to support sample weights.
  - **Pillar 3 (Large Datasets, $N \ge 50$)**: Uses **Elastic Net (`ElasticNet`)** with $L_1$-ratio $=0.2$ and $\alpha=0.1$ for sparse feature selection over high-dimensional vision space.
- **Tonal Math & Clamping**:
  - All regression targets use true mathematical defaults when missing ($1.0$ for right/bottom crop boundaries, $50.0$ for color grading blending, and linear $y=x$ control points for point curves).
  - Slider predictions are universally clamped to learned bounds (`slider_bounds`) recorded during training.
  - Recipe blending uses true linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$) rather than additive stacking.
- **Multi-Tiered Genre Classification (No Ad-Hoc Wordlists)**: NEVER implement hardcoded keyword exception arrays or custom string-matching lists inside filtering functions (like `_check_genre_mismatch`) to categorize photos or prevent cross-genre leakage. Instead, ALWAYS use the unified multi-tiered classification pipeline (`style_grouping._primary_genre_with_keywords`), which hierarchically evaluates explicit user keywords, vision model scene tags in confidence order, and EXIF Bayesian prior distributions (`_evaluate_exif_priors`, $\ge 0.30$).
- **Stitched Panoramas Exclusion**: Stitched panoramas (`_is_stitched_panorama`: `-Pano`/`_Pano` filename suffix, `panorama` tags, or aspect ratio $\ge 2.2:1$) must be universally filtered out of style upgrade recommendations and style training datasets.
- **Unified Visual-Semantic Verification**: To prevent cross-genre pollution (e.g. macro shots showing up in portrait or landscapes in street), ALWAYS verify both semantic and visual compatibility via `style_grouping.is_genre_compatible` and `style_grouping.verify_photo_visual_membership`. In view-time filtering or style upgrades, do not rely solely on tag string classification. Stricter visual verification thresholds (`>= 0.60`) must be applied to ambiguous or unknown genres.
- **Automated Rule Version Tracking & Semantic Cache Invalidation (`CURRENT_GROUPING_RULE_VERSION`)**:
  - **Troubleshooting Context**: During live debugging, stale entries in the SQLite `semantic_genre_cache` table caused endpoints like Upgrade Recommendations (`/styles/upgrades/recommendations`) to continue returning obsolete genre mappings (e.g., generic nature words mapping to `scene_wildlife`) even after the Python categorization logic had been fixed.
  - **Rule**: Whenever you modify categorization rules, dynamic mapping thresholds, or keyword guards in `style_grouping.py`, you MUST increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py`. This ensures that on startup or database connection, `catalog_service._ensure_initialized()` automatically purges stale entries (`DELETE FROM semantic_genre_cache`) and sets `NEEDS_REDISCOVERY = '1'` to trigger clean re-discovery.
  - **Lazy Route Initialization**: All backend routes querying styles or recommendations MUST call `catalog_service._ensure_initialized()` at their entry point so any pending migrations and cache wipes execute before candidate evaluation.
- **Hardware-Aware EXIF Evaluation & Crop Factors**: All focal length boundary checks (e.g., portrait `85-135mm`) MUST be evaluated against 35mm full-frame equivalents. Use `_get_35mm_equivalent_focal_length` in `style_grouping.py`, which parses `camera_make` and `camera_model` to apply sensor crop factors for Sony, Canon, Nikon, Fuji, OM System, and Leica.
- **Strict EXIF Macro Verification**: If a photo evaluates to `scene_macro`, it MUST be verified against the EXIF `lens` string. If a lens is explicitly logged but lacks `macro`, `micro`, or `mc` designations, macro-related tags must be stripped and the photo re-evaluated.
- **Subject Extraction Horizon**: When searching for overriding subject tags buried under noisy environment/background tags (like "nature"), always evaluate at least the top 6 vision tags (`content_tags[:6]`) to prevent subjects (e.g., "dog", "insect", "sports") from being ignored.

### ⚠️ Anti-Patterns & Positive Guidance (Lessons Learned)
To prevent recurring taxonomy and architecture regressions, strictly adhere to these DOs and DON'Ts:

- **DO NOT Short-Circuit Taxonomy Evaluation**: Never add early `return` statements for specific genres (like macro or landscape) that bypass keyword dictionaries or stronger subject overrides.
- **DO Evaluate the Full Subject Horizon**: Always let the unified classification pipeline process at least the top 6 vision tags (`content_tags[:6]`) before falling back to generic vision priors.
- **DO NOT Rely Solely on Raw Vision Confidence for Ambiguous Subjects**: The vision model cannot reliably distinguish semantically opposed but visually similar subjects (e.g., `wildlife` vs `pet` or `macro` vs `nature`).
- **DO Implement Explicit Hierarchical Overrides**: Always map specific domestic/human-centric tags (e.g., `domestic`, `dog`, `mammal`) to `scene_portrait` to force the pipeline to choose the correct intent over wild categories.
- **DO NOT Hardcode EXIF Hardware Bounds**: Never evaluate raw focal lengths against 35mm boundaries (e.g., `85 <= focal <= 135` for portrait) without first applying a sensor crop factor.
- **DO Use 35mm Equivalents**: Always use `_get_35mm_equivalent_focal_length` to parse `camera_make` and `camera_model` before applying Bayesian EXIF priors.
- **DO Explicitly Communicate Required User Actions**: Whenever making code changes that affect style grouping, keyword extraction, genre mapping, database schema, EXIF extraction, or ML training behavior, you MUST explicitly inform the user in your response what actions they need to take in Lightroom (e.g., clicking "Reset & Discover" in the Styles Index to re-bucket existing examples, or running "Train AI Style" to pull updated features like lens EXIF).
- **DO Include ALL Primary Regimes in Canonical Fallbacks**: Never omit primary domains (like `scene_macro` or `scene_nature`) from `canonical_regimes` sets in categorization logic. Omitting a primary category causes it to be unconditionally overwritten by whatever arbitrary background tags happen to follow it.
- **DO Extract Required EXIF Strings in Lua Plugin**: If the backend relies on EXIF strings like `lens` for hardware verification, you MUST ensure `Util.getPhotoExif` actually extracts and sends it. Missing data will cause the backend to fail open and skip verification logic.
- **DO Use Asymmetric Horizons for Suppressed Subjects**: Some subjects (like `dog`, `pet`, or `insect`) are heavily suppressed by environmental noise (`grass`, `nature`, `outdoors`) and may appear beyond index 6. When evaluating `nature` or `wildlife` tags, use a deeper horizon (`[:12]`) to find suppressed subjects without expanding the global tail-noise horizon.
- **DO NOT Hardcode Local API Ports**: Never assume local inference engines (like LM Studio) run on static default ports (e.g., `1234`). Always use the SDK's auto-discovery mechanisms (`find_default_local_api_host()`) to locate active dynamic/ephemeral ports.
- **DO NOT Use Standalone Providers for Local Models**: Avoid hardcoding standalone provider fallbacks (e.g., `qwen::`) for models executed via local runner APIs. They should be prefixed correctly based on the runner (e.g., `ollama::` or `lmstudio::`).
- **DO Manage Hidden UI State**: When hiding configuration sections in the Lightroom UI (e.g., via `visible = bind '...'`), always explicitly clear or reset the underlying boolean properties (like `enableMetadata`) in the mode-switching or reset handlers. Hidden fields that retain stale values or default to `false` can cause batch processing loops to silently skip essential steps.
- **DO Enforce Multi-Catalog Isolation**: Whenever querying ChromaDB (`collection.get()`) for features like Style Upgrade Recommendations, ALWAYS filter the results by the user's active `catalog_ids` to prevent cross-contamination from inactive catalogs.
- **DO Respect Visual Re-assignments at View-Time**: If a training example was visually reassigned to a different style cluster during the ML training pipeline (e.g., Pass 3 centroid distance), NEVER re-run raw text classification to filter it out at view-time. View-time queries must trust the database's `style_id` linkage.
- **DO Order Keyword Precedence**: Explicit dictionary keywords MUST take precedence (Step 1). Dynamic semantic vector mapping (SentenceTransformer) with strict cosine distance thresholds (<= 0.45) should also be evaluated in Step 1 alongside explicit keywords. HOWEVER, to prevent generic keywords (e.g., "vacation") from hijacking accurate tags, semantic mapping MUST only override the vision model if it maps to a **Specialized Subject Regime** (e.g., scene_astrophotography, scene_macro, scene_event). If it maps to a broad/environmental regime (e.g., scene_landscape, scene_nature), it acts as a weak fallback evaluated after the Vision Model and EXIF priors.
