# StyleAI Architecture & Data Pipelines

StyleAI leverages a split frontend/backend architecture to deliver local-first, privacy-centric AI capabilities to Adobe Lightroom Classic. This document outlines the core data pipelines, the divergence from legacy architectures (like LrGeniusAI), and integration guides for the underlying databases.

## 1. System Components

- **Frontend (Lightroom Plugin):** Written in Lua. Responsible for UI, extracting EXIF/develop settings, exporting temporary JPEG proxies, and applying generated recipes/masks via the LrC SDK.
- **Background Service (Python):** A completely local Python daemon (managed via `uv` and Flask). Handles heavy ML inference (SigLIP2, InsightFace), vector indexing (ChromaDB), structured metadata storage (SQLite), and API routing.

### Key Architectural Shifts from Legacy Implementations
1. **Vision Model Upgrade:** We replaced OpenCLIP with **SigLIP2** (`ViT-SO400M-16-SigLIP2-384`) for significantly better dense image understanding and zero-shot lighting classification.
2. **Predictive ML vs. Generative LLMs:** Legacy implementations relied on LLMs to interpolate edit sliders. StyleAI shifted to mathematically robust ML logic (KNN, Supervised Partial Least Squares, and Elastic Net Regression via Scikit-Learn) for applying edits, reserving LLMs purely as a "Creative Fallback."
3. **Asynchronous Pipelining:** The Python backend uses ThreadPoolExecutors to parallelize CPU preprocessing (decoding, hashing) and GPU/Neural Engine inference, drastically speeding up bulk indexing.
4. **Catalog ownership:** Each Lightroom catalog owns one adjacent `styleai.db` directory and local StyleAI backend state. Databases are never shared or routed across catalogs.

---

## 2. Core Pipelines

### A. Photo Analysis & Indexing Pipeline (Ingestion)
Builds the foundational search index and metadata repository.

1. **Lightroom (Client) - Fast Embeddings Phase:** `analyzeWorker` renders proxy JPEGs, extracts EXIF/keywords, and enqueues them asynchronously to `/index_queue`.
2. **CPU Preprocessing & Embeddings:** Python's `DynamicGPUWorker` batches these JPEGs, decodes them, and extracts 1152-dimensional SigLIP2 embeddings and InsightFace templates on the GPU. The embeddings are stored in ChromaDB and the raw images are cached in RAM.
3. **Lightroom (Client) - Metadata Phase:** A single local-LLM worker sends bounded batches only after each photo's embedding reaches a terminal state.
4. **Semantic Clustering & Inference:** The Python backend clusters the batch to deduplicate visually identical bursts, reducing LLM calls. Local LLM inference remains serialized to avoid GPU/unified-memory context thrash.

### B. Style Training Pipeline
Allows the system to mathematically learn your personal grading style without distortion from burst shooting or missing metadata.

1. **Lightroom (Client):** Evaluates a completed photo edit, extracts the Lightroom Develop settings (Recipe) along with star rating and pick flags, and sends them to the backend along with the JPEG proxy.
2. **Exposure Analysis:** The backend uses SigLIP2 to categorize the lighting and extracts raw pixel metrics (`zone_deep_shadows`, `histogram_signature`).
3. **Burst Curation & Density Weighting (Pillar 1):** During training, photos captured within $\Delta t \le 10\text{s}$ with SigLIP2 cosine distance $\le 0.05$ are grouped into burst clusters. The backend selects relative hero shots matching the highest star rating within the cluster (using pick flags and edit complexity as tie-breakers). Surviving hero shots share normalized density weight ($w_i = 1.0 / |C|$).
4. **Isolated Storage:** The embedding, exposure metrics, ratings, and recipe are saved to the **strictly isolated** `training_examples` ChromaDB collection. *This isolation ensures you can safely purge your search index without losing your precious ML training data.*

### C. AI Editing Pipeline
Applies predictive edits to new photos using dynamic regression architecture.

1. **Lightroom (Client):** Renders a proxy of an *unedited* photo and POSTs to `/edit_base64`.
2. **Evaluation:** The backend extracts the target's SigLIP2 embedding and exposure metrics.
3. **Prediction Engine:**
   - **High Volume ($N \ge 50$ examples - Pillar 3):** Uses **Elastic Net Regression** ($L_1$-ratio $=0.2$) with sample density weights to perform sparse feature selection over the 768d vision space and directly predict develop sliders.
   - **Medium Volume ($15 \le N < 50$ examples - Pillar 2):** Uses supervised **Partial Least Squares (`WeightedPLSRegression`)** with row scaling by $\sqrt{w_i}$ to project collinear vision embeddings and develop recipes into latent components that maximize predictive covariance.
   - **Low Volume ($N < 15$ examples):** Queries `training_examples` for KNN matches based on visual and exposure distance, mathematically interpolating the resulting recipes using linear interpolation ($\text{start} + \text{strength} \times (\text{target} - \text{start})$).
   - **Universal Clamping:** All slider predictions are strictly clamped to learned training boundaries (`slider_bounds`), preventing linear extrapolation on unusual lighting.
   - **Style Override:** Users can explicitly force a specific style profile, bypassing similarity searches.
   - **Generative Fallback:** If the ML engine has zero confidence, the system falls back to an LLM (if enabled) for a zero-shot creative edit.

### D. Embedding-First Style Verification Pipeline
Ensures visual consistency across discovery and upgrade recommendations without allowing noisy tags to become hard gates.

1. **Semantic labels and EXIF:** `_primary_genre_with_keywords` supplies an interpretable regime label, camera/profile filters, and a contradiction signal. It never independently admits a recommendation.
2. **Visual membership:** `verify_photo_visual_membership` evaluates normalized SigLIP2 similarity against training examples, with leave-one-out verification for discovery outliers and stricter evidence for semantic contradictions.
3. **Visual-cohesion splitting:** `split_examples_by_visual_cohesion` creates a distinct style only when a profile/genre group contains two or more dense components, each with at least two examples. Sparse and unembedded groups remain pooled.
4. **Diversity and burst curation:** Recommendation selection removes near-duplicate/burst frames, then ranks the remaining visual neighbors using hero quality and edited-state priority.

### E. Unified-Memory Resource Tiers

Indexing is bounded by physical-memory tiers to sustain throughput without MPS/LLM swap pressure. On Apple Silicon the defaults are GPU batch/queue/HTTP threads: 16 GB = `8/32/8`, 32 GB = `12/48/12`, and 64 GB+ = `16/64/16`. Explicit `STYLEAI_GPU_BATCH_SIZE`, `STYLEAI_INDEX_QUEUE_CAPACITY`, and `STYLEAI_HTTP_THREADS` overrides are reserved for measured tuning.

### F. Hardware-Aware EXIF Evaluation Pipeline
To accurately assign Bayesian priors, the system relies on hardware nomenclature translation rather than raw EXIF values.
1. **Sensor Crop Factor Conversion**: Because the plugin exports raw focal lengths (e.g. 45mm), the backend uses `_get_35mm_equivalent_focal_length` to parse `camera_make` and `camera_model`. This ensures OM System, Fuji, APS-C, and Medium Format shooters are evaluated fairly against full-frame boundaries (e.g. `85-135mm` for portraits).
2. **Strict Macro Lens Verification**: Photos categorized as `scene_macro` must pass an explicit regex check (`\b(macro|micro|mc)\b`) against their EXIF `lens` string. If a non-macro lens is detected, the category is stripped and the photo falls back to a secondary genre (e.g. `scene_nature`).

### G. Automated Semantic Caching & Rule Version Invalidation Pipeline
To prevent repetitive SigLIP2 embedding lookups for unknown user keywords during large catalog scans, `style_grouping._dynamic_semantic_mapping` caches closest semantic bucket matches inside the `semantic_genre_cache` table (`styles.sqlite`).

1. **Troubleshooting History & Why Cache Management is Critical**:
   - During production troubleshooting of cross-genre contamination (such as generic nature or trail words being categorized as `scene_wildlife`), developers discovered that updating classification functions or keyword guards in Python (`style_grouping.py`) alone did not resolve incorrect groupings.
   - Persistent entries stored in `semantic_genre_cache` survived server restarts and caused endpoints (including `/styles/upgrades/recommendations`) to immediately return stale, obsolete category mappings.
2. **Automated Rule Version Tracking (`CURRENT_GROUPING_RULE_VERSION`)**:
   - To guarantee synchronization between code rules and SQLite cache tables, `style_catalog.py` maintains a `CURRENT_GROUPING_RULE_VERSION` constant tracked inside the `grouping_rule_state` table.
   - Whenever categorization logic is modified, incrementing `CURRENT_GROUPING_RULE_VERSION` automatically triggers `catalog_service._ensure_initialized()` on startup or route invocation to:
     - Purge all entries from `semantic_genre_cache` (`DELETE FROM semantic_genre_cache`).
     - Flag `NEEDS_REDISCOVERY = '1'`, automatically scheduling a clean re-discovery of all existing style profiles against the updated rules.
3. **Lazy Initialization Hook**:
   - API endpoints that query recommendations or styles enforce a lazy call to `catalog_service._ensure_initialized()` at their entry point so cache wipes run before any evaluation.

---

## 3. Database Integration Guide

This section is for developers writing external tools or agents interacting with StyleAI data.

> [!CAUTION]
> **READ-ONLY ACCESS REQUIRED**
> External tools must **never** write, update, or delete records. Database population is strictly managed by the backend. Treat these endpoints as read-only to avoid corruption.

### Vector Store: ChromaDB
Stores visual embeddings, text metadata, and face templates in `<catalog parent>/styleai.db`.

#### Key Collections
1. `photos`: SigLIP2 embeddings for semantic search. `metadatas` contains stable Lightroom identifiers, capture metadata, keywords, and tags.
2. `faces`: InsightFace templates linked to `photo_id`.
3. `training_examples`: **Isolated** collection of SigLIP2 embeddings mapped directly to Lightroom Develop recipes for the Predictive ML engine.

### Relational Store: `styles.sqlite`
Stores the Auto-Discovered Style Profiles. Located in the same directory as ChromaDB.

#### Schema
- **`styles` table:** Contains the aggregated mathematical model of a style (e.g., `camera_model`, `mean_exposure_dna`, `develop_variance`).
- **`style_examples` table:** Junction table linking a `style_id` to the `photo_id`s in ChromaDB that trained it.
