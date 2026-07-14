# Developer Guide

Welcome to the StyleAI Developer Guide! This document provides upstream maintainers and curious contributors with an overview of the recent massive architectural refactoring, and details on how to build and extend the plugin.

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

To ensure Lightroom never hangs during batch processing, we abstracted all photo loops into `components/Pipeline.lua`.

**Key Features:**
- `Pipeline.runSequentialBatch(photos, progressScope, options, processFn)`
- Automatically wraps your `processFn` in an `LrTasks.pcall` to catch native crashes.
- Collects and tabulates successes and errors, returning them in a unified summary structure.
- When building new features that iterate over selected photos, ALWAYS use this pipeline.

## 3. SQLite Schema Migrations (`migrations/`)

StyleAI now uses a custom, lightweight Python migration engine to manage SQLite schema evolution without relying on heavy frameworks like Alembic.

**How to add a database column:**
1. Create a new Python file in `server/src/migrations/versions/` named `00X_description.py`.
2. Define a single `def upgrade(conn: sqlite3.Connection):` function.
3. Use the `conn` object to execute your `ALTER TABLE` statements.
4. The background service will automatically apply it the next time Lightroom binds.

## 4. ML vs LLM Abstraction (`providers/`)

With the shift away from generative models to mathematically robust machine learning for local edits, the architecture is now strictly bifurcated:

- **Predictive ML (Core):** Powered by local `scikit-learn` algorithms (KNN, Supervised Partial Least Squares, Elastic Net Regression) operating on SigLIP2 dense embeddings, burst-curated density weights, and raw exposure pixel metrics. This is the fast, primary method for style interpolation and slider prediction.
- **Generative LLMs (Fallback/Metadata):** LLMs are now exclusively used for zero-shot "Creative" fallback edits and generating semantic metadata (auto-tagging). The backend remains provider-agnostic. All providers inherit from `LLMProvider(ABC)`. To add a new provider (e.g. Anthropic Claude), simply subclass `LLMProvider` and implement `generate_metadata` and `test_connection`.

## 5. Security & Credentials

- **Backend Binding:** In production, the Flask background service unconditionally binds to `127.0.0.1` to prevent network exposure.
- **Keychain Storage:** We completely purged plaintext API keys from `Preferences.agprefs`. All API keys are securely retrieved via the native `LrPasswords` module in Lua. Never log or store API keys in plaintext files.

## 6. Observability

- **Diagnostic Reports:** Instead of asking users to zip up `.log` files, they can click "Generate Diagnostic Report" in the Lightroom Plugin Manager. This uses `TaskDiagnostics.lua` to pull backend `/health` and `/logs`, rendering a beautiful HTML file for instant browser viewing.

## 7. Pitfalls & C-Stack Overflows

Lightroom SDK plugins are prone to C-stack overflows if coroutine yields and database transactions are mismanaged.
- **NEVER yield inside a database transaction:** Do not call `LrTasks.yield()` inside `withWriteAccessDo` or `withPrivateWriteAccessDo` closures. Doing so suspends the Lua coroutine while holding a C-level SQLite transaction lock. The orphaned C-stack frames will rapidly accumulate and crash Lightroom.
- **The macOS Spin-Lock:** Using the pattern `if MAC_ENV then LrTasks.yield() else LrTasks.sleep(0.1) end` is a dangerous anti-pattern. On macOS, `LrTasks.yield()` without a subsequent sleep does NOT reliably return control to the Lightroom UI loop. During heavy batch processing, this causes C-stack buildup. ALWAYS use `LrTasks.yield(); LrTasks.sleep(0.01)` regardless of OS to guarantee the transaction stack flushes.
- **Transaction Bloat:** When applying multiple edits or metadata properties to a photo, combine them into a single `withWriteAccessDo` block. Firing multiple sequential database transactions per-photo exponentially increases SQLite overhead and stack pressure during batch operations.

## 8. Python Backend Memory Efficiency

When processing images in Python, particularly 1024px JPEGs received from Lightroom:
- **Lazy Downsampling:** ALWAYS call `image.thumbnail()` BEFORE calling `.convert("RGB")`. Calling `.convert("RGB")` on a full-resolution JPEG forces Pillow to decode and allocate the entire uncompressed 3-channel array in memory (which can consume hundreds of megabytes) before downsizing. `thumbnail()` allows Pillow to decode natively at a lower resolution, drastically reducing RAM footprints.
- **16-bit TIFF Bracketing:** The AI Edit bracket generation works with 16-bit ProPhoto RGB TIFFs. To prevent Out-Of-Memory (OOM) crashes during concurrent processing, TIFF concurrency is strictly capped to the number of CPU threads via `BRACKET_SEMAPHORE` in `image_processing.py`.

## 9. Signature Styles Export

Users can now export their learned Signature Styles directly to Lightroom Classic Develop Presets. The background service aggregates the style parameters into Adobe XMP format and generates an importable `.zip` file on the fly via `preset_generator.py`.

## 10. Lightroom SDK Layout Quirks

The Lightroom SDK `LrView` engine has several undocumented layout quirks, particularly when attempting to align different components like `popup_menu` and `simple_list`:
- **`share()` Truncation:** Using `width = share("groupName")` on mixed components can cause the layout engine to collapse to the narrowest intrinsic width among the shared elements. For example, a `popup_menu` will shrink to the width of its currently selected item, aggressively truncating the contents of a `simple_list` sharing the same width group.
- **`width_in_chars` on `simple_list`:** The `simple_list` component often completely ignores the `width_in_chars` parameter, collapsing to the natural width of its text content even if `width_in_chars` is generously set.
- **The Solution:** To guarantee perfectly aligned widths between different UI elements (e.g., centering a dropdown directly above a list), bypass dynamic text-width logic and hardcode an explicit pixel width (`width = 600`). This ensures all components stretch symmetrically regardless of their intrinsic text contents.

## 11. Multi-Tiered Genre Classification & Anti-Leakage Rules

When classifying photos or preventing cross-genre leakage (e.g., in Style Upgrade Assistant recommendations or AI Style Training):
- **DO NOT Use Ad-Hoc Keyword Wordlists:** NEVER implement hardcoded keyword exception arrays or custom string-matching lists inside filtering functions (like `_check_genre_mismatch`) to categorize photos. Ad-hoc wordlists do not scale across genres, languages, or evolving metadata.
- **USE the Unified Multi-Tiered Pipeline:** Always route candidate photos through the unified classification pipeline (`style_grouping._primary_genre_with_keywords`), which hierarchically evaluates:
  1. **Tier 1 (Explicit User Keywords):** Resolves dynamic semantic mappings and domain priority hierarchy from user metadata.
  2. **Tier 2 (Vision Model Content Tags):** Evaluates all predicted scene tags (`content_tags`) in confidence order against canonical subject regimes (`scene_studio`, `scene_macro`, `scene_portrait`, `scene_landscape`, `scene_architecture`, `scene_night`).
  3. **Tier 3 (EXIF Bayesian Priors):** When text or vision tags do not resolve a canonical regime, evaluates EXIF prior distributions (`_evaluate_exif_priors`, where strong priors $\ge 0.30$ determine regime from focal length, macro lenses, flash, or exposure settings).
- **Stitched Panoramas Exclusion:** Stitched panoramas (`_is_stitched_panorama`: `-Pano`/`_Pano` filename suffix, `panorama` tags, or aspect ratio $\ge 2.2:1$) must be universally filtered out of style upgrade recommendations and style training datasets.

## 12. Automated Rule Versioning & Semantic Cache Invalidation (`CURRENT_GROUPING_RULE_VERSION`)

To speed up classification across massive catalogs, dynamic semantic mappings (`_dynamic_semantic_mapping`) persist closest bucket lookups in the `semantic_genre_cache` table (`styles.sqlite`).

- **Troubleshooting History & Why Versioning is Critical:**
  During live debugging, modifying Python categorization logic (`style_grouping.py`) did not resolve cross-genre contamination on its own. Persistent loose semantic cache entries (such as nature or trail words mapped to `scene_wildlife`) survived restarts and caused endpoints like `/styles/upgrades/recommendations` to serve stale category mappings.
- **Rule Version Bumping:**
  Whenever categorization rules, distance thresholds, or guards change in `style_grouping.py`, developers **must** increment `CURRENT_GROUPING_RULE_VERSION` in `style_catalog.py`.
- **Automated Lifecycle Actions:**
  When `catalog_service._ensure_initialized()` executes on database open or API routing:
  1. It checks `SELECT rule_value FROM grouping_rule_state WHERE rule_key = 'GROUPING_RULE_VERSION'`.
  2. If the stored version differs from `CURRENT_GROUPING_RULE_VERSION`, it purges `semantic_genre_cache` (`DELETE FROM semantic_genre_cache`) and sets `NEEDS_REDISCOVERY = '1'`.
  3. `NEEDS_REDISCOVERY = '1'` triggers `discover_styles_from_examples()`, cleanly rebuilding all style buckets and cache entries against the latest logic.

