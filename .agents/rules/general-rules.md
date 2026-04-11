---
trigger: always_on
---

# LrGeniusAI General Development Rules

These rules ensure consistency across the Lightroom plugin and the Python backend.

## Error Handling & Logging
- **User-Facing Errors**: All errors and warnings from the backend must be surfaced in the Lightroom GUI using `ErrorHandler.handleError`. Avoid silent failures or generic messages.
- **Log Files**: Logs are for deep diagnostics. Manual inspection should be the last resort for users. Use `log:error`, `log:warn`, `log:info`, and `log:trace` consistently in the plugin.
- **Backend Logging**: Always use the configured `logger`. Include `exc_info=True` when logging exceptions.

## Plugin Development (Lua)
- **Asynchronicity**: Long-running operations must run in `LrTasks.startAsyncTask`.
- **Task Pattern**: Follow the `Task*.lua` naming convention for top-level plugin actions.
- **Yielding**: Use `LrTasks.pcall` instead of native `pcall` to allow for yielding during asynchronous operations.
- **Localization**: All GUI strings MUST be localized using the `LOC` function. Keep `TranslatedStrings_de.txt` (German) and `TranslatedStrings_fr.txt` (French) synchronized with the primary English strings.
- **Utilities**: Leverage `Util.lua` for common logic (e.g., table manipulation, stable photo IDs, file hashing).
- **Photo Identity**: Prefer the stable `globalPhotoId` (metadata-based) generated via `Util.getGlobalPhotoIdForPhoto` for cross-catalog consistency.

## Backend Development (Python/Flask)
- **Structure**: Organize endpoints using Flask Blueprints (`routes_*.py`). Keep business logic in the service layer (`service_*.py`).
- **API Response Format**: Return structured JSON. Standard fields include `results`, `error`, and `warning` (actionable short message for the GUI).
- **Environment**: Configuration should be driven by environment variables (e.g., `GENIUSAI_PORT`, `GENIUSAI_BACKUP_ENABLED`).
- **Lifecycle**: Respect `server_lifecycle.py` for PID management and "OK" file signaling.

## Infrastructure & Testing
- **Docker**: Always update `Dockerfile`, `docker-compose-dev.yml`, and `docker-compose-prod.yml` when changing dependencies or environment requirements.
- **Smoke Tests**: Maintain and expand `TaskAutomatedTests.lua` to verify plugin-backend connectivity and core utility integrity.
- **API Stability**: Ensure changes to the backend API are reflected in the plugin's `APISearchIndex.lua` and smoke tests.

## Plugin platform detection
- There are two globally defined booleans WIN_ENV and MAC_ENV.

## Translations
- Always update all three translation files: TranslatedString_*.txt