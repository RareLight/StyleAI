## [v2.13.0-pre] - 2026-04-11

- docs: add privacy policy document outlining local-first data handling practices
- Add GNU AGPL v3 license
- refactor: update shutdown logic to unload resources from both local and remote backends
- feat: add /unload API endpoint to free model and collection memory without stopping the server fix: JSON decode error popups in a loop in plug-in manager while backend not ready.
- fix: update Windows installer architecture compatibility and improve build artifact output handling
- chore: add SourceDir directive to Windows installer configuration
- feat: add application icon to plugin and server directories
- fix: disable MSYS path conversion for Inno Setup compiler to prevent path resolution errors
- refactor: make DB_PATH handling robust against uninitialized states across services
- feat: implement dynamic database initialization, add server restart/init API endpoints, and introduce macOS/Windows installer build workflows.
- feat: disable onboarding wizard display during plugin initialization
- refactor: improve system health diagnostics, update onboarding strings, and enforce provider configuration checks in AI tasks.
- feat: implement onboarding wizard and system health diagnostics for backend and model configuration
- refactor: implement granular file-by-file model downloading to improve progress tracking accuracy
- fix: add progress tracking and improve error handling for log file export process
- feat: import share from LrView in DevelopEditManager
- refactor: standardize EXIF extraction, update UI layout constraints, and modernize style engine test suite
- fix: pass nil headers to LrHttp.get to correctly apply timeout parameter
- refactor: replace pcall with LrTasks.pcall and improve error handling in _request function
- refactor: improve HTTP request robustness with pcall and add AI engine/base profile metadata to the UI
- feat: add trace logging for API request results and status headers
- feat: add camera distribution tracking to training service and display learned cameras in plugin UI, plus add logging safety checks
- fix: add error handling for server log retrieval in copyLogfilesToDesktop task
- feat: reduce CLIP status polling frequency and throttle log output for status changes
- fix: run log file copy operations in asynchronous tasks to prevent UI blocking
- refactor: unify server log collection to exclusively use API-fetched logs with dynamic hostname prefixing
- feat: update tech stack documentation, add Credits wiki page, and improve remote log file naming conventions
- feat: implement remote log collection and error reporting for backend services
- feat: implement style engine tracking and UI dashboard for training profile statistics
- test: update search mock return values, fix edit endpoint payload keys, and mock send_file for database backups
- feat: implement full localization support for UI strings and dialog messages across the plugin
- refactor: update API test mocks to reflect route refactoring, add general project rules, and remove deprecated pcall rule
- feat: concatenate error list into a single string for improved error reporting
- fix: wrap Ollama provider initialization in try-block to prevent startup crashes
- feat: add warning reporting for indexing tasks and improve server health check logic
- feat: add scope selection to training dialog to allow processing of selected photos, current view, or entire catalog
- feat: add training photo validation and display helpful UI hints for edit style learning
- chore: add docker-compose.yml to .gitignore renamer docker-compose.yml to docker-compose-prod.yml
- feat: add user-facing warnings for unindexed or invalid reference photos in similarity search
- feat: add support for saving and applying custom user edit styles via training examples chore: missing translations

## Installers & System Integration
- The backend now runs as a persistent system service (LaunchAgent on macOS, Startup Registry on Windows).
- It starts automatically at login and remains active to manage background AI tasks even when Lightroom is closed.
- Manual management (troubleshooting):
  - Windows: Run `{commonpf}\LrGeniusAI\backend\lrgenius-server.cmd`
  - macOS: Service `com.lrgenius.server` (managed via `launchctl`)

### Security & Permissions
- **Windows**: You may see a SmartScreen warning ("Windows protected your PC") during installation because the installer is not signed. Click "More info" and "Run anyway" to proceed.
- **macOS**: If Gatekeeper blocks the installer, go to **System Settings > Privacy & Security** and click **"Open Anyway"** under the Security section. Alternatively, run `xattr -d com.apple.quarantine <path-to-pkg>` in Terminal to clear the block.

## Docker Deployment
- For containerized environments, use the `LrGeniusAI-plugin-docker-backend-<version>.zip` asset which includes the pre-configured plugin and Docker setup.
