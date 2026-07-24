# 🖥️ StyleAI - Background Service

This is the Python-based core of **StyleAI**. It acts as the local bridge between Adobe Lightroom Classic (Lua) and our various ML/AI pipelines, handling image processing, metadata storage, predictive styling, and high-speed semantic search.

---

## 🛠️ Core Responsibilities

- **📂 Database Management:** Stores image metadata, AI-generated descriptions, and vector embeddings in a local SQLite database and ChromaDB vector store to ensure blazing-fast retrieval without re-scanning images.
- **🧠 ML / AI Orchestration:** 
    - Executes completely local ML prediction for personalized editing (KNN, Supervised Partial Least Squares, Elastic Net Regression) with burst deduplication and density weighting.
    - Interfaces with **Local LLMs** (Ollama, LM Studio) for creative generative fallback edits and auto-tagging.
- **🔎 Semantic Search Engine:** Uses `SigLIP2` to generate dense visual image embeddings locally. This allows users to search their Lightroom catalog using natural language descriptions instead of just keywords.
- **🎭 Face Intelligence:** Provides face detection and recognition capabilities powered entirely locally by `InsightFace`.
- **⚙️ Metadata Sync:** Handles the import of existing keywords and metadata from Lightroom to build a comprehensive search index.

---

## 🚀 Technical Architecture

The backend is built with **Python**, managed via `uv`, and designed to run as a local background process.

### Key Components:
- **API Framework:** Flask-based REST interface for communication with the Lightroom Lua plugin.
- **Computer Vision:**
    - `SigLIP2`: For generating local visual semantic embeddings.
    - `InsightFace`: For advanced face detection and recognition.
- **Database:** SQLite (Metadata & ML Style Models) and ChromaDB (Vector Store).
- **Task Handling:** Asynchronous `ThreadPoolExecutor` architecture for efficient background processing without blocking Lightroom.

---

## 🗄️ Database API

The background service exposes dedicated database endpoints for status and backup operations.

### `GET /db/stats`
Returns aggregated counters for the current backend databases, including total indexed photos, faces, and style training examples.

### `GET /db/backup`
Creates and returns a ZIP backup of the persistent backend data directory. This includes the Chroma data as well as accompanying JSON and SQLite files stored under the configured DB path.

### Lightroom plugin integration
In `Plug-in Manager -> StyleAI -> Background Service`, the button `Download DB backup` downloads this ZIP from the backend and reveals the saved file.

---

## ⚠️ Identity Scope Note

The backend uses `photo_id` / hashes derived from the Lightroom catalog. While highly stable, backend identity should still be treated as best-effort and mostly catalog-scoped, especially when:
- The same source files exist in multiple Lightroom catalogs.
- Files were duplicated, re-exported, or rewritten outside Lightroom.
- Stable metadata IDs were unavailable, falling back to partial file hashes.
