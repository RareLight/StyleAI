# StyleAI UI Overhaul — Human Test Matrix

Use a backed-up disposable Lightroom Classic catalog. Test the release
configuration first (`developerBuild=false` in both manifest and
`BuildConfig.lua`). Do not confirm deletion, pruning, or restore actions against
the production catalog.

## 1. Release navigation and Settings & Status

1. Install/reload the plugin and open **File > Plug-in Extras** from both the
   Library and Develop modules.
2. Confirm the six production commands appear in the documented order, all
   dialog-opening commands use ellipses, no duplicates appear in Export, and no
   developer commands appear in Help.
3. Open Plugin Manager and verify overall, background service, vision model,
   optional metadata model, learned-style status, versions, and catalog-local
   database path are readable without relying on color.
4. Resize the Plugin Manager pane narrow and wide. Verify paths and explanatory
   text wrap or elide without pushing buttons off-screen.
5. Verify backup/restore, statistics, prune, restart, diagnostics, logs, credits,
   documentation, license, and training deletion remain reachable.
6. Open every destructive confirmation and cancel it. Confirm the exact scope
   is stated and no data changes.

## 2. Debug-off and Debug-on capture

1. Start from an upgraded preference set with legacy `auditLlmInputs=true` and
   a legacy destination. Confirm **Enable Debug options** and capture both load
   off while the old destination is retained.
2. With Debug off, run metadata generation through Ollama and LM Studio. Confirm
   no default debug directory is created and no diagnostic files are written.
3. Enable Debug only. Confirm subordinate controls appear without a blank band
   when hidden again, and confirm metadata generation still writes no captures.
4. Enable capture. Generate metadata and verify one request group contains the
   expected original/bracket files in the displayed local destination.
5. Confirm Reveal opens the destination. Create an unrelated file there, use
   Clear, and verify captured groups are removed while the unrelated file
   remains.
6. Disable Debug and run metadata again. Confirm capture stops immediately.

## 3. Prepare Photos

1. Test Analyze only, metadata only, and both. Compare task behavior with
   `UI_BEHAVIOR_CONTRACTS.md`.
2. Verify neither task can proceed, metadata cannot proceed without a local
   model or output field, and missing/unprocessed scope cannot select Replace.
3. Test selected, current view, entire catalog, missing, and indexed scopes.
4. Test keep/replace StyleAI data and catalog append/replace on known metadata.
   Verify only the fields named in the warning are replaced.
5. Toggle metadata off and confirm its tabs collapse without leaving a large
   empty region.
6. Test prompt add, rename, delete, duplicate/blank rejection, restore Default,
   and Cancel. Confirm Cancel does not save prompt edits.
7. Test long prompt text, long model names, long paths, hierarchy options,
   bilingual keywords, and per-photo context reuse.

## 4. Learn From My Edits

1. Test selected, view, and catalog scopes with a mix of RAW, DNG, JPEG, TIFF,
   video, panorama, and already-learned photos.
2. Confirm the selection snapshot survives focus changes caused by the modal.
3. Test **Update previously learned examples** off and on.
4. Observe preparation, upload, policy fitting, validation/activation, success,
   failure, and cancellation states. Confirm exactly one rebuild follows a
   completed upload and the previous generation remains active on failure.

## 5. Apply My Style and review

1. Verify scope/count appears first and that virtual copies, per-photo review,
   masks, crop, and straighten match existing effective preferences.
2. Confirm crop and straighten default off for a fresh preference set.
3. Exercise profile/HDR Off, Suggest, and Auto without a local metadata LLM;
   trained editing should remain available when the vision model is ready.
4. For all safety combinations, compare the bound summary with the actual
   target (original/copy), review behavior, mask behavior, crop, and rotation.
5. Verify recipe details are read-only and scrollable; global and mask choices
   remain adjacent; match text is understandable without color.
6. Test Apply, cancel/discard, Lightroom Undo, history reconciliation, and
   explicit accepted/modified/not-useful outcomes on no more than 100 selected
   tracked photos.

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

## 7. Platform and accessibility matrix

Repeat the layout pass for every supported language and with deliberately long
paths, model names, prompt names, filenames, status errors, and descriptions.

| Platform | Required configurations |
| --- | --- |
| macOS Tahoe or newer | Light, dark, increased contrast; small laptop; scaled display; external display; move dialogs between displays |
| Windows 11 | 100%, 125%, 150%, and 200% scaling; light and dark |

For each configuration, check keyboard traversal, focus after hiding Debug,
button reachability, wrapped text, clipped controls, overlapping controls,
scroller access, dialog resize behavior, and status meaning without color.

## 8. Developer-build smoke suite

In a separate development package, set both developer-build constants true.
Confirm the four Help commands and one-time style override appear, then run
**Developer: Run Automated Tests...**. Record Lightroom version, operating
system, display scale, language, pass/fail totals, and the first error for any
failure. Restore both constants to false before release packaging.
