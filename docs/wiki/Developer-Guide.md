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
4. The backend will automatically apply it the next time Lightroom binds to the server.

*Example:* See `003_add_user_style_name.py` for how we added custom style renaming capabilities.

## 4. LLM Provider Abstraction (`providers/`)

The backend is completely provider-agnostic. Whether a user connects to a local `Ollama` instance or cloud `Gemini`, the core logic never changes.

- All providers inherit from `LLMProvider(ABC)`.
- Use the `get_analysis_service(config)` factory function to obtain the active provider based on the user's settings.
- To add a new provider (e.g. Anthropic Claude), simply subclass `LLMProvider` and implement `generate_metadata` and `test_connection`.

## 5. Security & Credentials

- **Backend Binding:** In production, the Flask server unconditionally binds to `127.0.0.1` to prevent network exposure.
- **Keychain Storage:** We completely purged plaintext API keys from `Preferences.agprefs`. All API keys are securely retrieved via the native `LrPasswords` module in Lua. Never log or store API keys in plaintext files.

## 6. Observability

- **Diagnostic Reports:** Instead of asking users to zip up `.log` files, they can click "Generate Diagnostic Report" in the Lightroom Plugin Manager. This uses `TaskDiagnostics.lua` to pull backend `/health` and `/logs`, rendering a beautiful HTML file for instant browser viewing.
