---
trigger: always_on
---

# StyleAI General Development Rules

These rules ensure consistency across the Lightroom plugin and the Python backend.

## Error Handling & Logging
- **User-Facing Errors**: All errors and warnings from the backend must be surfaced in the Lightroom GUI using `ErrorHandler.handleError`. Avoid silent failures or generic messages.
- **Log Files**: Logs are for deep diagnostics. Manual inspection should be the last resort for users. Use `log:error`, `log:warn`, `log:info`, and `log:trace` consistently in the plugin.
- **Backend Logging**: Always use the configured `logger`. Include `exc_info=True` when logging exceptions.

## Plugin Development (Lua)
- **Asynchronicity**: Long-running operations must run in `LrTasks.startAsyncTask`.
- **Task Pattern**: Follow the `Task*.lua` naming convention for top-level plugin actions.
- **Yielding & C-Stack Overflows**: 
  - Use `LrTasks.pcall` instead of native `pcall` to allow for yielding during asynchronous operations.
  - **CRITICAL**: NEVER call `LrTasks.yield()` inside `withWriteAccessDo` or `withPrivateWriteAccessDo` closures. Doing so while holding a C-level transaction lock will cause fatal C-stack overflows.
  - **macOS Spin-Locks**: NEVER use `if MAC_ENV then LrTasks.yield() else LrTasks.sleep(0.1) end`. On macOS, `yield()` without sleep fails to return control to the UI scheduler, causing C-stack accumulation during batches. ALWAYS use `LrTasks.yield(); LrTasks.sleep(0.01)` to ensure proper flushing.
  - **Transaction Bloat**: Consolidate multiple database transactions into a single `withWriteAccessDo` block per photo to minimize SQLite queue overhead.
- **Localization**: All GUI strings MUST be localized using the `LOC` function. Keep `TranslatedStrings_de.txt` (German) and `TranslatedStrings_fr.txt` (French) synchronized with the primary English strings.
- **Utilities**: Leverage `Util.lua` for common logic (e.g., table manipulation, stable photo IDs, file hashing).
- **Photo Identity**: Prefer the stable `globalPhotoId` (metadata-based) generated via `Util.getGlobalPhotoIdForPhoto` for cross-catalog consistency.
- **Proactive Nil Checks & Refactoring**: Because Lua lacks strict typing, runtime `nil` errors are extremely common. When deleting or refactoring methods, parameters, or variables, you MUST execute a project-wide search to ensure you remove ALL lingering references across other files. Additionally, preemptively initialize empty property tables and settings with default safe values to prevent 'attempt to index global / local nil value' errors.

## Backend Development (Python/Flask)
- **Structure**: Organize endpoints using Flask Blueprints (`routes/*.py`). Keep business logic in the service layer (`services/*.py`).
- **API Response Format**: Return structured JSON. Standard fields include `results`, `error`, and `warning` (actionable short message for the GUI).
- **Environment**: Configuration should be driven by environment variables (e.g., `STYLEAI_PORT`, `STYLEAI_BACKUP_ENABLED`).
- **Memory Efficiency**: ALWAYS call `Image.thumbnail()` BEFORE `.convert("RGB")` when processing user images. Calling `.convert("RGB")` first allocates the full uncompressed array in RAM, leading to severe OOM spikes during batch processing.
- **Lifecycle**: Respect `server_lifecycle.py` for PID management and "OK" file signaling.
- **Code Style**: Code should be formatted with `uv run ruff format` and should have no errors from `server/scripts/lint_format.sh`.

## Infrastructure & Testing
- **Docker**: Always update `Dockerfile`, `docker-compose-dev.yml`, and `docker-compose-prod.yml` when changing dependencies or environment requirements.
- **Smoke Tests**: Maintain and expand `TaskAutomatedTests.lua` to verify plugin-backend connectivity and core utility integrity.
- **API Stability**: Ensure changes to the backend API are reflected in the plugin's `APISearchIndex.lua` and smoke tests.

## Plugin platform detection
- There are two globally defined booleans WIN_ENV and MAC_ENV.

## Translations
- Always update all three translation files: TranslatedString_*.txt

## Database Architecture
- **Isolation**: Keep ChromaDB collections `photos` (Semantic Search) and `training_examples` (Style Training) strictly isolated. Never merge them. This protects precious ML training data when a user bulk-deletes their search index.

## Lightroom SDK UI Quirks
- **`share()` Truncation**: When attempting to match widths between mixed UI elements (like `popup_menu` and `simple_list`), using `width = share("...")` will often aggressively collapse the layout to the width of the narrowest intrinsic element (e.g., the selected dropdown item).
- **`width_in_chars` Failures**: `simple_list` frequently ignores the `width_in_chars` parameter and will collapse to its raw text content width, breaking column alignments.
- **Alignment Solution**: To ensure identical element widths and perfectly center components within a dialog, avoid fluid bounds entirely. Wrap the elements in a centered column (using `fill_horizontal = 1` spacers) and assign explicit pixel widths to the components (e.g., `width = 600`).

## ML Training Curation & Regression Rules
- **Burst Curation**: All style training pipelines MUST execute burst clustering ($\Delta t \le 10\text{s}$ and SigLIP2 cosine distance $\le 0.05$) to prevent repetitive frames from skewing model weights. Relative hero shots within a burst are selected by maximum star rating, pick flag status (`pick_status == 1`), and edit complexity, sharing normalized density weight ($w_i = 1.0 / |C|$).
- **Supervised PLS vs Elastic Net**: When training style models, use **Partial Least Squares (`WeightedPLSRegression`)** with row scaling ($X \odot \sqrt{w}, Y \odot \sqrt{w}$) for small datasets ($15 \le N < 50$), and **Elastic Net (`ElasticNet`)** with $L_1$-ratio $=0.2$ for large datasets ($N \ge 50$). Never use unsupervised PCA for style target regression.
- **Tonal Math Defaults & Clamping**: Always use true mathematical defaults for missing regression targets ($1.0$ for right/bottom crop boundaries, $50.0$ for color grading blending, and linear $y=x$ control points for point curves). Universally clamp inference predictions to learned training bounds (`slider_bounds`) and blend recipes using linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$).
- **Database Update Instructions**: Whenever making code changes that affect style grouping, keyword extraction, genre mapping, or ML training/regression behavior, you MUST explicitly inform the user in your summary what action is required in Lightroom to synchronize their database (e.g., clicking **"Discover"** in the Styles Index to re-bucket existing examples vs. running **"Train AI Style"** for changes requiring pixel feature re-extraction).
- **Multi-Tiered Genre Classification (No Ad-Hoc Wordlists)**: NEVER implement hardcoded keyword exception arrays or custom string-matching lists inside filtering functions (like `_check_genre_mismatch`) to categorize photos or prevent cross-genre leakage. Instead, ALWAYS use the unified multi-tiered classification pipeline (`style_grouping._primary_genre_with_keywords`), which hierarchically evaluates explicit user keywords, vision model scene tags in confidence order, and EXIF Bayesian prior distributions (`_evaluate_exif_priors`, $\ge 0.30$).
- **Stitched Panoramas Exclusion**: Stitched panoramas (`_is_stitched_panorama`: `-Pano`/`_Pano` filename suffix, `panorama` tags, or aspect ratio $\ge 2.2:1$) must be universally filtered out of style upgrade recommendations and style training datasets.
- **Unified Visual-Semantic Verification**: To prevent cross-genre pollution (e.g. macro shots showing up in portrait or landscapes in street), ALWAYS verify both semantic and visual compatibility via `style_grouping.is_genre_compatible` and `style_grouping.verify_photo_visual_membership`. In view-time filtering or style upgrades, do not rely solely on tag string classification. Stricter visual verification thresholds (`>= 0.60`) must be applied to ambiguous or unknown genres.
- **Automated Rule Version Tracking & Semantic Cache Invalidation (`CURRENT_GROUPING_RULE_VERSION`)**:
  - **Troubleshooting Context**: During live debugging, stale entries in the SQLite `semantic_genre_cache` table caused endpoints like Upgrade Recommendations (`/styles/upgrades/recommendations`) to continue returning obsolete genre mappings (e.g., generic nature words mapping to `scene_wildlife`) even after the Python categorization logic had been fixed.
  - **Rule**: Whenever you modify categorization rules, dynamic mapping thresholds, or keyword guards in `style_grouping.py`, you MUST increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py`. This ensures that on startup or database connection, `catalog_service._ensure_initialized()` automatically purges stale entries (`DELETE FROM semantic_genre_cache`) and sets `NEEDS_REDISCOVERY = '1'` to trigger clean re-discovery.
  - **Lazy Route Initialization**: All backend routes querying styles or recommendations MUST call `catalog_service._ensure_initialized()` at their entry point so any pending migrations and cache wipes execute before candidate evaluation.


