# StyleAI UI Overhaul Implementation Checklist

> Historical note: the build-separation items below were superseded in August
> 2026 by one canonical plug-in manifest and the runtime **Enable Developer
> Options** gate. See `UI_BEHAVIOR_CONTRACTS.md` for current behavior.

Status: implemented; remaining unchecked items require human Lightroom/platform
validation. This file is a historical implementation record, not the current
architecture source of truth. See `UI_BEHAVIOR_CONTRACTS.md`, `AGENTS.md`, and
the code for current contracts.

## Product and safety rules

- [x] Organize the product around Settings & Status, Prepare Photos, Learn From
  My Edits, Apply My Style, Styles & Training, and Rate Selected AI Edits.
- [x] Keep all LLM integrations loopback-only and local by default.
- [x] Treat diagnostic input capture as Debug-only and disabled by default.
- [x] Separate runtime Debug options from developer-build-only commands.
- [x] Prefer dynamic layout and native Lightroom controls.
- [x] Use explicit pixel sizes only for verified Lightroom layout quirks,
  bounded previews, compact numeric controls, and tested minimum bounds.
- [x] Keep unresolved product choices, including `replaceSS` and top-level
  keyword customization, behaviorally unchanged until explicitly approved.
- [x] Do not remove or obscure catalog-changing behavior.

## Phase 1: Baseline and contracts

- [x] Inventory production menu commands and backing task files.
- [x] Inventory Plugin Manager sections, dialogs, confirmations, progress
  scopes, and user-visible messages.
- [x] Map each control to its property, preference, request field, and catalog
  or Develop side effect.
- [x] Classify preferences as active, hidden-active, ineffective, unused, or
  migration-only.
- [x] Record representative Prepare and Apply request payloads.
- [x] Record catalog effects for metadata append/replace, validation, virtual
  copies, crop/rotation, training, and edit-outcome review.
- [x] Add automated contract tests where behavior is testable outside
  Lightroom and extend Lightroom smoke-test coverage where it is not.

Exit gate: redesigned controls can be compared with the current behavior.

## Phase 2: Shared dynamic UI foundation

- [x] Add reusable UI helpers for sections, form rows, explanatory text,
  statuses, notices, destructive actions, scope selectors, run summaries, and
  empty/loading/error states.
- [x] Default primary containers and long fields to `fill_horizontal = 1`.
- [x] Default lists, detail areas, and scrollers to `fill_vertical = 1` where
  supported.
- [x] Use `f:control_spacing()` instead of repeated manual spacer values.
- [x] Use wrapped text and vertical growth instead of forcing dialog width.
- [x] Make content-heavy dialogs resizable.
- [x] Use bounded scrollers for unbounded content.
- [x] Share label widths only among homogeneous form labels.
- [x] Avoid shared widths across mixed control types.
- [x] Avoid overlapping branches for ordinary conditional forms.
- [x] Use explicit dimensions only for verified SDK exceptions and document
  each exception.
- [x] Ensure hidden controls cannot retain unintended active state.
- [x] Standardize primary, secondary, cancel, and destructive action wording.
- [x] Ensure color never carries status meaning by itself.
- [x] Localize every visible label, action, tooltip, and progress caption.

Exit gate: shared primitives render correctly in a small macOS and Windows
Lightroom test dialog.

## Phase 3: Unified Debug policy

- [x] Add `debugMode`, default false.
- [x] Add subordinate `captureLlmInputs`, default false.
- [x] Require both settings before saving diagnostic image inputs.
- [x] Apply the same gate to indexing, Ollama, LM Studio metadata generation, and
  future providers.
- [x] Remove unconditional LM Studio debug-cache writes.
- [x] Stop creating debug directories during ordinary backend startup.
- [x] Create debug destinations lazily on first authorized capture.
- [x] Replace platform-specific fallback paths with a platform-neutral StyleAI
  support location.
- [x] Validate and normalize custom capture destinations.
- [x] Bound retention by capture count and total bytes.
- [x] Treat all files for one request as a single retention unit.
- [x] Add Reveal and Clear Captured Debug Data actions.
- [x] Confirm before clearing and report the result.
- [x] Never delete existing captures automatically during migration.
- [x] Migrate legacy `auditLlmInputs=true` to Debug off and capture off while
  retaining the old path for possible later reuse.
- [x] Reset effective capture to false immediately when Debug is disabled.
- [x] Make the backend ignore legacy audit flags without the master debug gate.
- [x] Keep runtime Debug separate from Flask `--debug`.
- [x] Avoid prompts, responses, photo IDs, and paths in INFO-level logs.

Exit gate: a default release creates no debug directory and writes no
diagnostic image or prompt copies.

## Phase 4: Menus and build separation

- [x] Make File > Plug-in Extras the canonical cross-module workflow menu.
- [x] Rename commands to the approved workflow language.
- [x] Use ellipses consistently for dialog-opening commands.
- [x] Remove duplicate Export-menu commands.
- [x] Remove developer commands from release manifests.
- [x] Register automated tests, benchmark, rendering spike, and reconciliation
  only in developer builds.
- [x] Do not dynamically mutate registered menus from `debugMode`.
- [x] Mark developer builds clearly in Settings & Status.
- [x] Keep diagnostic input capture off by default even in developer builds.

Exit gate: release menus contain only end-user workflows and every supported
workflow remains reachable.

## Phase 5: Settings & Status

- [x] Present concise overall, background-service, vision-model, and optional
  metadata-provider statuses.
- [x] Provide one contextual repair action per problem.
- [x] Stop health polling when the Plugin Manager closes.
- [x] Keep only genuine cross-workflow defaults in normal settings.
- [x] Explain catalog-local storage and show the resolved database location.
- [x] Group backup and restore together with their scope explanation.
- [x] Move prune, restart, statistics, diagnostics, and training deletion into
  Maintenance.
- [x] Separate destructive actions visually and confirm their exact scope.
- [x] Replace the large credits block with compact About information.
- [x] Show plugin/backend versions and documentation/license links.
- [x] Add an Advanced checkbox named Enable Debug Options.
- [x] Put all subordinate Debug controls in one conditional group at the end.
- [x] Ensure hiding the group leaves no empty row, separator, or width demand.
- [x] Add the local-data warning, capture toggle, abbreviated destination,
  Choose, Reveal, retention summary, and Clear actions.
- [x] Clear focus before hiding a focused Debug control.

Exit gate: all existing maintenance/support behavior remains available and
Debug off leaves a balanced production layout.

## Phase 6: Prepare Photos

- [x] Replace the mode popup plus synchronized task switches with two outcome
  choices: Analyze Photos for StyleAI and Generate Keywords and Descriptions.
- [x] Preserve the existing backend task combinations.
- [x] Prevent proceeding with no task selected.
- [x] Show model readiness beside the task that requires it.
- [x] Present scope and resolved count where safely available.
- [x] Present skip-existing and replace-existing as clear exclusive choices.
- [x] Resolve incompatible scope/processing choices before submission.
- [x] Add a dynamic run summary and show a resolved count where safe. Keep the
  modal primary verb concise because Lightroom does not dynamically bind it.
- [x] Hide metadata configuration when metadata generation is off.
- [x] Group generated fields and conditionally enable keyword-only settings.
- [x] Preserve current top-level keyword and `replaceSS` behavior pending
  product decisions.
- [x] Present append/replace as positive radio choices.
- [x] Make overwrite warnings field-specific.
- [x] Redesign prompt add/rename/delete/restore behavior safely.
- [x] Clarify GPS, existing keywords, folder names, and user context.
- [x] Correct the misleading per-batch/per-photo context wording.
- [x] Remove the broad Reset All Defaults action.
- [x] Compare old/new task arrays, API options, and catalog writes.

Exit gate: equivalent choices produce equivalent backend and catalog results.

## Phase 7: Learn From My Edits

- [x] Show scope, estimated count, RAW/DNG eligibility, and exclusions.
- [x] Rename force retraining to Update Previously Learned Examples.
- [x] Explain precisely what is replaced and what remains untouched.
- [x] Remove the broad reset action.
- [x] Show the selected count beside scope and use a concise primary action;
  resolve larger scopes only after commitment to avoid blocking the dialog.
- [x] Preserve batching, durable jobs, selection snapshots, cancellation, and
  exactly one rebuild after a complete upload.
- [x] Distinguish preparation, upload, policy building, and activation in
  progress and completion messages.

Exit gate: training records and policy activation behavior are unchanged.

## Phase 8: Apply My Style

- [x] Show scope and the selected count first; resolve broader/eligible counts
  after commitment so the options dialog stays responsive.
- [x] Restore visible Create Virtual Copies and Review Each Edit controls
  controls without changing existing users' effective values.
- [x] Present strength, profile, and HDR behavior with concise explanations.
- [x] Keep crop and straighten separate and off by default.
- [x] Add a dynamic safety summary covering originals/copies, review, crop,
  and rotation.
- [x] Use a concise primary action and put dynamic count/safety information in
  the bound summary because Lightroom's modal action verb is static.
- [x] Remove the production debug style override.
- [x] Audit legacy adjustment preferences and retain only controls whose
  backend semantics are supported and tested.
- [x] Preserve full-strength idempotence, application interpolation, history,
  and operation lifecycle behavior.

Exit gate: every catalog-changing behavior is visible and every retained
option is honored.

## Phase 9: Review experiences

- [x] Make edit and metadata review dialogs resizable.
- [x] Use dynamic preview/detail layouts and bounded scrollers.
- [x] Present match status as text plus optional color without implying
  unproven probability calibration.
- [x] Keep global and mask application choices adjacent.
- [x] Make recipe details read-only and scrollable.
- [x] Preserve keyword selection/editing and generated/de-cluttered comparison.
- [x] Clarify Save Following Without Review and Discard behavior.
- [x] Replace edit-outcome popup with described radio choices.
- [x] State that one outcome applies to all tracked selected photos and that
  rejection does not undo an edit.
- [x] Preserve the 100-photo bound and modeled-slider readback.

Exit gate: all review decisions and consequences are explicit.

## Phase 10: Styles & Training

- [x] Extract shared style list/filter/detail logic.
- [x] Build one resizable list-and-detail workspace.
- [x] Preserve deterministic selection after filtering.
- [x] Show name, profile/rendering partition, example count, policy type,
  evidence cues, and description.
- [x] Keep cues explanatory rather than membership labels.
- [x] Preserve Show Photos, Rename, Find Examples, recommendation feedback,
  and collection creation behavior.
- [x] Put rebuilding and deletion in a separate Maintenance area.
- [x] Preserve custom names and keep feedback evaluation-only.
- [ ] HUMAN: Verify empty, loading, rebuilding, failure, and large-list states
  in Lightroom with a representative catalog.

Exit gate: both former management workflows remain fully available.

## Phase 11: Legacy cleanup and documentation

- [x] Classify proposed preference removals as safe-delete, migration-only,
  active, or compatibility-reserved.
- [x] Add migrations before deleting renamed or structurally changed keys.
- [x] Remove unused Plugin Manager state, export sizing, indexing batch sizing,
  and ineffective controls after repository-wide verification; retain stored
  compatibility defaults to avoid destructive upgrade churn.
- [x] Remove commented-out UI and orphaned localization keys.
- [x] Update documentation, menu names, and help text. No repository screenshot
  assets currently exist to update.
- [x] Record preference migrations and relocated controls in release notes.

Exit gate: no stale references remain and upgrades preserve effective behavior.

## Phase 12: Automated and human validation

- [x] Run Lua parse/static checks, localization-key checks, Ruff checks, and the
  backend test suite.
- [ ] HUMAN: Run the developer-build Lightroom automated plugin tests.
- [x] Compare representative old/new request fields and catalog side effects in
  `UI_BEHAVIOR_CONTRACTS.md`; retain unchanged orchestration paths.
- [ ] HUMAN: Verify selection snapshots, cancellation, write-transaction rules,
  backups, history, and destructive confirmations.
- [ ] HUMAN: Verify Debug-off and Debug-on capture behavior through Lightroom;
  backend gate, retention, and maintenance contracts are automated.
- [ ] HUMAN: Verify dynamic layouts with long paths, names, prompts, and translations.
- [ ] HUMAN: Test macOS Tahoe or newer in light, dark, increased-contrast, small
  laptop, scaled, and external-display configurations.
- [ ] HUMAN: Test Windows 11 at 100%, 125%, 150%, and 200% scaling.
- [ ] HUMAN: Test every supported language and empty/loading/error/large-catalog state.

Release gate: automated checks pass and Lightroom-specific macOS/Windows human
testing finds no critical clipping, inaccessible behavior, or regression.
