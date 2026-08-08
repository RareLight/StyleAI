# 🖥️ StyleAI - Background Service

This is the Python-based core of **StyleAI**. It acts as the local bridge between Adobe Lightroom Classic (Lua) and our various ML/AI pipelines, handling image processing, metadata storage, predictive styling, and high-speed semantic search.

---

## 🛠️ Core Responsibilities

- **📂 Database Management:** Stores image metadata, AI-generated descriptions, and vector embeddings in a local SQLite database and ChromaDB vector store to ensure blazing-fast retrieval without re-scanning images.
- **🧠 ML / AI Orchestration:** 
    - Executes local source-conditioned editing-policy inference with target-behavior discovery, burst-grouped estimator selection, hierarchical calibration, burst curation, and ambiguity-aware abstention.
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

### `POST /db/backup`
Creates a validated ZIP backup of the persistent backend data directory at the
local `output_path` supplied by the Lightroom plugin. The archive includes a
versioned manifest and checksums alongside Chroma, SQLite, and policy artifacts.

### `POST /db/restore`
Validates and restores a same-catalog backup. Restore rejects unsafe archive
paths, corruption, checksum failures, unsupported formats, and backups owned by
a different catalog. A required pre-restore snapshot and atomic rollback protect
the current database.

### Lightroom plugin integration
In `Plug-in Manager -> StyleAI -> Data & Recovery`, use `Export Backup...` to
save an external copy or `Restore Backup...` to start the guarded restore flow.
These backups cover StyleAI data only, not the Lightroom catalog or Develop
edits.

---

## ⚠️ Identity Scope Note

The backend uses stable catalog `photo_id` values. A backend process and its
adjacent `styleai.db` belong to exactly one Lightroom catalog; cross-catalog
database sharing and routing are unsupported.
