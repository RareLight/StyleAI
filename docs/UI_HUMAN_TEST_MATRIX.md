# StyleAI UI Human Test Matrix

Use a backed-up disposable Lightroom Classic catalog. Test the checked-in
release configuration first (`developerBuild=false` in `BuildConfig.lua`). Do
not confirm deletion, pruning, or restore actions against the production
catalog.

## 1. Release navigation and Settings & Status

1. Install/reload the plugin and open **File > Plug-in Extras** from both the
   Library and Develop modules.
2. Confirm the six production commands appear in the documented order, all
   dialog-opening commands use ellipses, no duplicates appear in Export, and no
   developer commands appear in Help.
3. Open Plugin Manager and verify the Status & Setup, Styles, Data & Recovery,
   Support & Debug, Updates, and About sections appear in that order. Confirm
   overall, background service, vision model, optional metadata model,
   learned-style counts, versions, and the abbreviated catalog-local data path
   are readable without relying on color.
4. Resize the Plugin Manager pane narrow and wide. Verify paths and explanatory
   text wrap or elide without pushing buttons off-screen.
5. With the service running, confirm Repair Background Service is hidden. Stop
   the service, reopen Plugin Manager, confirm Repair appears, and verify it
   restores service readiness without losing settings.
6. Verify Reveal Data Folder, Export Backup, Restore Backup, Clean Up Removed
   Photos, Generate Support Report, Open Logs Folder, credits, documentation,
   and license remain reachable. Confirm no database-statistics, preview-source,
   legacy restart, or training-deletion control remains in Plugin Manager.
7. Open Styles & Training from Plugin Manager. Confirm Delete All Training Data
   appears only in its maintenance area and its scope is clear.
8. Enable Debug and verify the processing choices are Automatic, Lower Resource
   Use, and Faster. Confirm Debug off leaves no empty or unbalanced layout area.
9. Open every destructive confirmation and cancel it. Confirm the exact scope
   is stated and no data changes.

## 2. Debug-off and Debug-on capture

1. Start from an upgraded preference set with legacy `auditLlmInputs=true` and
   a legacy destination. Confirm **Enable Debug options** and capture both load
   off while the old destination is retained.
2. With Debug off, run metadata generation through Ollama and LM Studio. Confirm
   no default debug directory is created and no diagnostic files are written.
3. Enable Debug only. Confirm subordinate controls appear without a blank band
   when hidden again, and confirm metadata generation still writes no captures.
4. Enable capture. Generate metadata and verify each accepted photo creates its
   own original diagnostic image in the displayed local destination.
5. Confirm Reveal opens the destination. Create an unrelated file there, use
   Clear, and verify captured groups are removed while the unrelated file
   remains.
6. Disable Debug and run metadata again. Confirm capture stops immediately.

## 3. Prepare Photos

1. Test Analyze only, metadata only, and both. Compare task behavior with
   `UI_BEHAVIOR_CONTRACTS.md`.
2. Verify neither task can proceed, metadata cannot proceed without a local
   model or output field, and missing/unprocessed scope cannot select Replace.
3. Test selected, entire catalog, missing, and indexed scopes.
4. Test keep/replace StyleAI data and catalog append/replace on known metadata.
   Verify only the selected generated fields are replaced.
5. Toggle metadata off and confirm the primary window remains compact. Toggle
   it on, open Metadata Settings, and verify output, model/instructions, and
   context tabs are available without enlarging the primary window.
6. Test prompt add, rename, delete, duplicate/blank rejection, restore Default,
   and Cancel. Confirm canceling Metadata Settings restores its entry-state
   values and canceling Prepare Photos does not save prompt edits.
7. Test long prompt text, long model names, long paths, hierarchy options,
   bilingual keywords, and per-photo context reuse.
8. Confirm the initial window uses a compact reading width, the Scope menu does
   not span the window, status text wraps, and resizing wider does not disturb
   alignment.
9. Rerun a fully complete selection and confirm it reports a successful no-op
   with deduplicated unique counts and performs no preview extraction.
10. Include originals plus virtual copies and verify progress/final totals do
    not double-count them or any local/backend failure.
11. Cancel while metadata waits for embeddings and while a local-model batch is
    active. Confirm a second operation proceeds without restarting the service.

## 4. Learn From My Edits

1. Test selected and catalog scopes with a mix of RAW, DNG, JPEG, TIFF,
   video, panorama, and already-learned photos.
2. Confirm the selection snapshot survives focus changes caused by the modal.
3. Test **Update previously learned examples** off and on.
4. Confirm the introduction and supporting text use readable regular system
   text, wrap within the initial window, and do not force a screen-wide modal.
5. Observe preparation, upload, policy fitting, validation/activation, success,
   failure, and cancellation states. Confirm exactly one rebuild follows a
   completed upload and the previous generation remains active on failure.
6. Test more than 5,000 eligible source IDs and originals with virtual copies.
   Confirm preflight continues in bounded pages and reports skipped duplicate
   source instances without rejecting the workflow.

## 5. Apply My Style and review

1. Verify scope/count appears first and that virtual copies, per-photo review,
   crop, and straighten match existing effective preferences. Confirm no learned
   mask control appears.
2. Confirm virtual copies default on for a fresh preference set, then confirm
   both enabling and disabling the option persist after a completed dialog.
   Crop and straighten should still default off.
3. Exercise profile/HDR Off, Suggest, and Auto without a local metadata LLM;
   trained editing should remain available when the vision model is ready.
4. For all safety combinations, compare the bound summary with the actual
   target (original/copy), review behavior, crop, and rotation.
5. Verify recipe details are read-only and scrollable; the global choice and
   match text are understandable without color.
6. Test Apply, cancel/discard, Lightroom Undo, history reconciliation, and
   explicit accepted/modified/not-useful outcomes on no more than 100 selected
   tracked photos.
7. Confirm Rate Selected AI Edits opens at a compact reading width with a clear
   Outcome section and no single line determines screen-wide geometry.
8. With virtual copies enabled, confirm edits affect only the copies, every
   created copy appears in one new uniquely named `StyleAI <YYMMDD-HHMMSS>`
   collection, the completion dialog names it, and Lightroom does not switch
   the active source to that collection.
9. Train with a representative mix of uncropped, cropped-only, rotated-only,
   and cropped-and-rotated photos. With both permissions enabled, confirm crop
   and rotation are independently omitted unless their learned applicability
   gate passes; enabling either option must not force that action on every photo.
10. Compare independent (`STYLEAI_EDIT_BURST_COHERENCE=0`) and optimized runs
    on sports, wildlife, event, panning, bracketed, lighting-transition,
    subject-entry/exit, crop-change, and mixed-camera sequences. Confirm only
    admitted members show the localized coherent-burst context.
11. Confirm burst members retain their own review choice, crop/rotation,
    profile/HDR, virtual copy, application receipt, Develop readback,
    progress/error accounting, and source order. Cancel during an in-flight
    batch and verify already applied handoffs finish while new work stops.
12. In a developer build only, enable
    `STYLEAI_EDIT_BURST_EXACT_REUSE=1` and compare its output with independent
    inference. Confirm white balance, geometry, profile/HDR, and sparse
    families remain photo-specific; test cached/uncached RAW previews, missing
    previews, large bursts, and constrained/critical memory pressure.

## 6. Styles & Training

1. Verify empty, loading, populated, filtered, rebuild-running, rebuild-failed,
   and large-style-list states.
2. Confirm filtering preserves a valid deterministic selection.
3. Verify name, rendering partition/profile, examples, policy type, evidence
   cues, and description are readable at narrow and wide sizes.
4. Test Show Photos, Show All Photos, Rename, Find More Training Examples,
   candidate collection creation, and all three evaluation-only feedback labels.
5. Confirm custom names survive rebuild and feedback does not automatically
   change thresholds or active models.
6. Verify Find More Training Examples uses a compact informational window when
   no styles need examples. With populated recommendations, verify the shorter
   list, details-first flow, and Candidate Review actions remain usable.

## 7. Platform and accessibility matrix

Repeat the layout pass for every supported language and with deliberately long
paths, model names, prompt names, filenames, status errors, and descriptions.

| Platform | Required configurations |
| --- | --- |
| macOS Tahoe or newer | Light, dark, increased contrast; small laptop; scaled display; external display; move dialogs between displays |
| Windows 11 | 100%, 125%, 150%, and 200% scaling; light and dark |

For each configuration, check keyboard traversal, focus after hiding Debug,
button reachability, regular-size supporting text, wrapped text, bounded popup
menus, clipped controls, overlapping controls, scroller access, dialog resize
behavior, and status meaning without color.

## 8. Developer-build smoke suite

Create a separate development package with
`python scripts/package_lrc_plugin.py developer`. Confirm the four Help commands
appear, then run
**Developer: Run Automated Tests...**. Record Lightroom version, operating
system, display scale, language, pass/fail totals, and the first error for any
failure. The generated package lives under ignored `build/`; the checked-in
release manifest remains unchanged.
