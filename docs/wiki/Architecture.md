# StyleAI Architecture & Data Pipelines

StyleAI leverages a split frontend/backend architecture to deliver local-first, privacy-centric AI capabilities to Adobe Lightroom Classic. This document outlines the core data pipelines, the divergence from legacy architectures (like LrGeniusAI), and integration guides for the underlying databases.

## 1. System Components

- **Frontend (Lightroom Plugin):** Written in Lua. Responsible for UI, extracting EXIF/develop settings, exporting temporary JPEG proxies, and applying generated recipes/masks via the LrC SDK.
- **Background Service (Python):** A completely local Python daemon (managed via `uv` and Flask). Handles heavy ML inference (SigLIP2, InsightFace), vector indexing (ChromaDB), structured metadata storage (SQLite), and API routing.

### Key Architectural Shifts from Legacy Implementations
1. **Vision Model Upgrade:** We replaced OpenCLIP with **SigLIP2** (`ViT-SO400M-16-SigLIP2-384`) for significantly better dense image understanding and zero-shot lighting classification.
2. **Predictive ML vs. Generative LLMs:** Legacy implementations relied on LLMs to interpolate edit sliders. StyleAI shifted to mathematically robust ML logic (KNN and Ridge Regression via Scikit-Learn) for applying edits, reserving LLMs purely as a "Creative Fallback."
3. **Asynchronous Pipelining:** The Python backend uses ThreadPoolExecutors to parallelize CPU preprocessing (decoding, hashing) and GPU/Neural Engine inference, drastically speeding up bulk indexing.
4. **Catalog Isolation:** A single StyleAI backend can serve multiple `.lrcat` files using soft-state `catalog_ids` tags in ChromaDB.

---

## 2. Core Pipelines

### A. Photo Analysis & Indexing Pipeline (Ingestion)
Builds the foundational search index and metadata repository.

1. **Lightroom (Client):** Renders a proxy JPEG, extracts EXIF/keywords, and POSTs to `/index_base64`.
2. **CPU Preprocessing:** Background threads decode the JPEG and calculate an MD5 hash.
3. **Vision & Face Inference:** 
   - **SigLIP2:** Extracts a 1152-dimensional dense embedding (categorizing scene lighting and semantics).
   - **InsightFace:** Extracts 512-dimensional face templates and bounding boxes.
4. **Storage:** Vectors are pushed to the `photos` and `faces` collections in ChromaDB.

### B. Style Training Pipeline
Allows the system to mathematically learn your personal grading style.

1. **Lightroom (Client):** Evaluates a completed photo edit, extracts the Lightroom Develop settings (Recipe), and sends it to the backend along with the JPEG proxy.
2. **Exposure Analysis:** The backend uses SigLIP2 to categorize the lighting and extracts raw pixel metrics (`zone_deep_shadows`, `histogram_signature`). 
3. **Isolated Storage:** The embedding, exposure metrics, and recipe are saved to the **strictly isolated** `training_examples` ChromaDB collection. *This isolation ensures you can safely purge your search index without losing your precious ML training data.*

### C. AI Editing Pipeline
Applies predictive edits to new photos.

1. **Lightroom (Client):** Renders a proxy of an *unedited* photo and POSTs to `/edit_base64`.
2. **Evaluation:** The backend extracts the target's SigLIP2 embedding and exposure metrics.
3. **Prediction Engine:**
   - **High Volume (>20 examples):** Bypasses retrieval and uses Ridge Regression/PCA to infer slider values directly from the model.
   - **Low Volume (<20 examples):** Queries `training_examples` for KNN matches based on visual and exposure distance, mathematically interpolating the resulting recipes.
   - **Style Override:** Users can explicitly force a specific style profile, bypassing similarity searches.
   - **Generative Fallback:** If the ML engine has zero confidence, the system falls back to an LLM (if enabled) for a zero-shot creative edit.

---

## 3. Database Integration Guide

This section is for developers writing external tools or agents interacting with StyleAI data.

> [!CAUTION]
> **READ-ONLY ACCESS REQUIRED**
> External tools must **never** write, update, or delete records. Database population is strictly managed by the backend. Treat these endpoints as read-only to avoid corruption.

### Vector Store: ChromaDB
Stores visual embeddings, text metadata, and face templates. Located typically at `~/.gemini/antigravity/StyleAI/db`.

#### Key Collections
1. `photos`: SigLIP2 embeddings for semantic search. `metadatas` contains `catalog_ids`, keywords, and tags.
2. `faces`: InsightFace templates linked to `photo_id`.
3. `training_examples`: **Isolated** collection of SigLIP2 embeddings mapped directly to Lightroom Develop recipes for the Predictive ML engine.

### Relational Store: `styles.sqlite`
Stores the Auto-Discovered Style Profiles. Located in the same directory as ChromaDB.

#### Schema
- **`styles` table:** Contains the aggregated mathematical model of a style (e.g., `camera_model`, `mean_exposure_dna`, `develop_variance`).
- **`style_examples` table:** Junction table linking a `style_id` to the `photo_id`s in ChromaDB that trained it.
