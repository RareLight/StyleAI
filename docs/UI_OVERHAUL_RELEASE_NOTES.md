# UI Overhaul Release Notes

These notes describe the completed 0.8-era UI transition. For the maintained
UI contract, use `UI_BEHAVIOR_CONTRACTS.md`. The former developer/release build
separation described below was superseded by the single-build, dynamically
hidden Plug-in Manager Developer Options panel in August 2026.

## Navigation and workflow names

- Lightroom's cross-module **File > Plug-in Extras** menu is now the canonical
  entry point, with one command set and no module-specific duplicate.
- The primary workflows are now **Prepare Photos**, **Learn From My Edits**,
  **Apply My Style**, **Rate Selected AI Edits**, **Styles & Training**, and
  **Find More Training Examples**.
- Developer tests and capability tools are absent from release builds. They
  remain available only when the manifest and `BuildConfig.developerBuild`
  developer-build constants are enabled together during development packaging.

## Settings and moved controls

- Plugin Manager now leads with explicit background-service, vision-model,
  optional metadata-provider, and learned-style status.
- Catalog-local data location, backup/restore scope, maintenance tools,
  versions, support, and compact About information are grouped by purpose.
- Virtual-copy, review-before-apply, mask, crop, and straighten behavior is
  visible in **Apply My Style** with an effective safety summary.
- Broad reset actions were removed from Prepare and Learn. Destructive actions
  state their exact scope and require confirmation.

## Plugin Manager simplification

- Split the former combined configuration form into Status & Setup, Styles,
  Data & Recovery, Support & Debug, Updates, and About sections.
- Replaced count-derived style-quality claims with factual saved-example and
  active-style counts.
- Moved Delete All Training Data into Styles & Training maintenance.
- Retained validated backup export, protected restore, and removed-photo
  cleanup while removing the non-actionable database-statistics button.
- Replaced Restart Service with a health-aware repair action that can also
  start an offline service.
- Made Lightroom preview use automatic with the existing export fallback.
- Moved processing-load overrides under Debug and removed the Maximum choice.
- Consolidated diagnostic details and available logs into one support-report
  folder.

## Compact workflow windows

- Prepare Photos now keeps its primary decision window limited to tasks, photo
  scope, existing-data handling, and the effective run summary. Optional local
  metadata controls open in a focused Metadata Settings dialog.
- Find More Training Examples now resolves its recommendation state before
  constructing the window. Empty results no longer allocate list and detail
  panels; populated results use a shorter list and place review actions after
  the selected policy details.
- Workflow dialogs now open at a bounded reading width, wrap descriptions
  within that geometry, and use compact popup menus instead of stretching them
  across the window. Resizable workspaces can still grow on larger displays.
- Supporting descriptions now use Lightroom's regular system text by default
  for improved readability.

## Debug and preference migration

- **Enable Debug options** defaults off. Diagnostic LLM image capture is hidden
  and disabled until Debug is explicitly enabled, and capture itself must then
  be enabled separately.
- Existing `auditLlmInputs=true` values are not carried forward. A legacy custom
  destination is retained for possible reuse, but both new gates migrate off.
- Diagnostic directories are created lazily, retention is bounded, and Clear
  removes only recognized StyleAI capture groups.
- The ineffective Plugin Manager fields for export sizing, indexing batch size,
  and force-fresh previews were removed. Stored legacy values are not
  destructively deleted.

## Compatibility

Metadata append/replace semantics, `replaceSS`, top-level keyword behavior,
training operation lifecycle, editing-policy generation, Develop application,
history, review outcomes, and catalog write behavior remain unchanged unless
explicitly described above. See `UI_BEHAVIOR_CONTRACTS.md` for the detailed
mapping and deferred product decisions.
