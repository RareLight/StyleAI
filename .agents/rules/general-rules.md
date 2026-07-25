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
  - **Shutdown Blocking (CRITICAL)**: Never wrap Lightroom's shutdown `doneFunc` in native `pcall`. Lightroom's shutdown sequence yields under the hood; wrapping it in native `pcall` causes a fatal C-boundary yield error that aborts the sequence, hanging Lightroom indefinitely. Always use `LrTasks.pcall(doneFunc)`.
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
- **LLM Inference Batching (CRITICAL)**: LLM metadata requests from Lua MUST be batched (e.g. sending arrays of 32 photos to `/metadata/generate_batch`). Never dispatch metadata requests sequentially using the single `/metadata/generate` route when processing batches. Sequential fetching bypasses backend Semantic Clustering, forcing the LLM to process every image individually and destroying inference throughput on fast hardware.
- **Supervised PLS vs Elastic Net**: When training style models, use **Partial Least Squares (`WeightedPLSRegression`)** with row scaling ($X \odot \sqrt{w}, Y \odot \sqrt{w}$) for small datasets ($15 \le N < 50$), and **Elastic Net (`ElasticNet`)** with $L_1$-ratio $=0.2$ for large datasets ($N \ge 50$). Never use unsupervised PCA for style target regression.
- **Tonal Math Defaults & Clamping**: Always use true mathematical defaults for missing regression targets ($1.0$ for right/bottom crop boundaries, $50.0$ for color grading blending, and linear $y=x$ control points for point curves). Universally clamp inference predictions to learned training bounds (`slider_bounds`) and blend recipes using linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$).

- **Multi-Tiered Genre Classification (No Ad-Hoc Wordlists)**: NEVER implement hardcoded keyword exception arrays or custom string-matching lists inside filtering functions (like `_check_genre_mismatch`) to categorize photos or prevent cross-genre leakage. Instead, ALWAYS use the unified multi-tiered classification pipeline (`style_grouping._primary_genre_with_keywords`), which hierarchically evaluates explicit user keywords, vision model scene tags in confidence order, and EXIF Bayesian prior distributions (`_evaluate_exif_priors`). Note: Only `scene_night` (score 0.40) and `scene_macro` (score 0.35) can independently trigger EXIF-based classification at Step 3 (floor ≥ 0.30). Other EXIF priors (`scene_portrait` 0.15, `scene_landscape` 0.15, `scene_studio` 0.20) are below this floor and serve only as disambiguation signals within the vision model processing (e.g., macro-prior disambiguation for ambiguous `scene_nature` photos).
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

- **DO NOT Bypass Semantic Clustering with Sequential LLM Requests**: When writing plugins or scripts to fetch metadata, never iterate over items and call `/metadata/generate` sequentially. This bypasses the deduplication pipeline and artificially bottlenecks the GPU. Always batch requests and send them to `/metadata/generate_batch`.
- **DO NOT Use Native `pcall` in Lightroom Teardown**: Never use native `pcall` to wrap Lightroom's internal `doneFunc` or shutdown tasks. Lightroom's SDK teardown sequence yields, and native `pcall` causes a C-stack crash that hangs the entire Lightroom application. Always use `LrTasks.pcall`.
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
