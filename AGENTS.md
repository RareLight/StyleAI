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
- **Supported LLMs**: Google Gemini, OpenAI/ChatGPT, Ollama, LM-Studio.

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
│       └── providers/             # LLM provider implementations (gemini, chatgpt, ollama)
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


