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
│   └── src/
│       ├── styleai_server.py      # Server entry point
│       ├── server_lifecycle.py    # Process PID & OK file signalling, idle unloading
│       ├── routes/                # Flask Blueprints (HTTP endpoints)
│       ├── services/              # Business logic (chroma, index, search, face, style_engine)
│       └── providers/             # LLM provider implementations (gemini, chatgpt, ollama)
├── docs/wiki/                     # GitHub Wiki source pages (auto-published)
└── .agents/rules/                 # Always-on constraint files for agents
```

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
