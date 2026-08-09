# Plugin Manager Simplification Checklist

Status: implemented; remaining unchecked items require human Lightroom/platform
validation. This is a historical implementation record. Current behavior is
defined by `UI_BEHAVIOR_CONTRACTS.md` and the production code.

## Product rules

- [x] Keep the first visible section focused on current system readiness and
  actionable setup or repair.
- [x] Prefer factual counts and states over inferred quality labels.
- [x] Keep rare but essential recovery operations available through progressive
  disclosure.
- [x] Move style-specific destructive actions into Styles & Training.
- [x] Keep Debug controls hidden, subordinate, and off by default.
- [x] Preserve existing backup validation, restore rollback, pre-prune backup,
  and catalog-ownership checks.
- [x] Remove UI-only legacy state without deleting compatibility preferences or
  backend endpoints that remain useful for diagnostics and automation.
- [x] Use dynamic rows and wrapped text; do not place long text and several
  buttons on one fixed horizontal row.

## Phase 1: Behavior inventory and safety gates

- [x] Map every retained action to its existing Lua client and backend route.
- [x] Confirm manual backup exports a validated StyleAI-only archive.
- [x] Confirm restore remains same-catalog validated with rollback protection.
- [x] Confirm cleanup removes only records for photos absent from the active
  Lightroom catalog and creates a pre-cleanup backup.
- [x] Confirm training deletion removes examples and learned styles but not
  search embeddings, Lightroom photos, or Develop edits.
- [x] Record that database statistics remain available to tests/diagnostics
  after their Plugin Manager button is removed.

## Phase 2: Status and style summary

- [x] Retain overall, service, vision-model, and optional metadata-model status.
- [x] Show Repair Background Service only while the service is unreachable.
- [x] Keep model configuration/setup reachable with clearer wording.
- [x] Replace count-derived High Precision/Good Matching labels with saved
  example and active-style counts.
- [x] Remove descriptor, camera, and top-style detail lists from Plugin Manager.
- [x] Remove the manual style-statistics Refresh button.
- [x] Add an Open Styles & Training action using the existing workflow.

## Phase 3: Data and recovery

- [x] Create a dedicated Data & Recovery Plugin Manager section.
- [x] Show an abbreviated catalog-local data path and a Reveal Data Folder
  action instead of a disabled full-width editor.
- [x] Rename Download Backup to Export Backup...
- [x] Retain Restore Backup... with its existing scope confirmation.
- [x] Rename Prune Database to Clean Up Removed Photos...
- [x] Remove Show DB Stats from the user-facing UI only.
- [x] Remove Delete All Training Data from Plugin Manager.
- [x] Add Delete All Training Data to the Styles & Training maintenance area
  with the existing confirmation and backend behavior.

## Phase 4: Performance and preview controls

- [x] Remove the normal Use Lightroom previews checkbox; previews should be
  automatic with the existing export fallback and timeout circuit breaker.
- [x] Stop Plugin Manager from writing a default performance value merely
  because the dialog was opened.
- [x] Move processing-load control under Debug and reduce it to Automatic,
  Lower Resource Use, and Faster.
- [x] Preserve existing explicit profiles through a safe compatibility mapping;
  remove the Maximum choice from UI.

## Phase 5: Support, Debug, updates, and About

- [x] Consolidate diagnostics, logs, and Debug into Support & Debug.
- [x] Replace Copy logfiles to Desktop with Generate Support Report..., backed
  by a folder containing system details plus available plugin/provider logs.
- [x] Retain Open Logs Folder as a secondary support action.
- [x] Keep Debug capture destination, Reveal, retention summary, and safe Clear.
- [x] Do not check for updates on opening Plugin Manager when automatic checks
  are disabled.
- [x] Keep manual Check for Updates and rename the preference to Automatically
  check for updates.
- [x] Keep About compact: plugin/service versions, documentation, credits, and
  license without a wide text-plus-buttons row.
- [x] Remove dead Plugin Manager logging state.

## Phase 6: Localization and documentation

- [x] Localize all new labels, statuses, confirmations, and progress text.
- [x] Remove orphaned Plugin Manager localization keys where they are no longer
  referenced anywhere.
- [x] Update behavior contracts, release notes, and the human-test matrix for
  automatic previews,
  relocated training deletion, and the simplified maintenance surface.
- [x] Synchronize English, German, French, Spanish, and Catalan resources.

## Phase 7: Validation and handoff

- [x] Parse every Lua file with `luac -p`.
- [x] Run focused `luacheck` on changed Lua files and resolve new errors,
  undefined variables, and unsafe nil access.
- [x] Run the Lightroom plugin validator and localization-key audit.
- [x] Run targeted backend tests for health, backup, restore, prune, training
  deletion, and diagnostic capture behavior.
- [x] Redeploy only after Lightroom has fully exited.
- [ ] HUMAN: Verify Plugin Manager section disclosure, wrapping, path display,
  Debug hiding, repair behavior, and destructive confirmations in Lightroom.
- [ ] HUMAN: Verify macOS Tahoe-or-newer layout and Windows scaling matrix.
