# StyleAI UI Behavior Contracts

This is the current UI-to-behavior contract, based on Lua orchestration and
backend request parsing. The completed implementation checklists are historical
records; this document and the code define current behavior.

## Production navigation

The canonical cross-module entry point is **File > Plug-in Extras**.

| Command | Backing task | User-visible outcome |
| --- | --- | --- |
| Prepare Photos... | `TaskAnalyzeAndIndex.lua` | Visual analysis, local metadata generation, or both |
| Learn From My Edits... | `TaskTrainFromEdits.lua` | Save eligible RAW/DNG edits as training examples and rebuild once after upload |
| Apply My Style... | `TaskAiEditPredictive.lua` / `AiEditAction.lua` | Infer and optionally review/apply learned Develop edits |
| Rate Selected AI Edits... | `TaskReviewAIEditOutcome.lua` | Persist explicit evaluation-only outcomes for at most 100 tracked photos |
| Styles & Training... | `TaskStyleCatalog.lua` | Browse, rename, reveal, and rebuild learned styles |
| Find More Training Examples... | `TaskDiscoverUpgradeCandidates.lua` | Review policy-specific candidate photos and record evaluation-only feedback |

The canonical plug-in does not register developer Help commands because
Lightroom cannot hide static manifest entries from a runtime preference.
**Enable Developer Options** in Plug-in Manager defaults off; turning it on
immediately reveals developer-tool buttons in **Support & Debug**, and turning
it off hides them. Every tool also fails closed through that preference. Debug
options and Developer options are separate visibility gates.

**Compare Local Metadata Models...**
snapshots the current photo selection before opening its dialog, creates a
uniquely named collection under **StyleAI → Benchmarks**, prepares each proxy
once, and runs the same bytes successively through every selected local vision
model. Generated fields are never written to Lightroom, Chroma, or learned
editing state. Incremental JSONL plus comparison CSV, summary CSV, manifest, and
HTML reports are written beneath the catalog-local
`styleai.db/evaluation_reports` directory and revealed when the run finishes or
stops with partial results.

## Control-to-behavior mapping

### Prepare Photos

| UI choice | Preference / request behavior | Side effect |
| --- | --- | --- |
| Analyze photos for StyleAI | `enableEmbeddings`; legacy `indexingMode` remains synchronized | Commits local image embeddings before dependent metadata work |
| Generate keywords and descriptions | `enableMetadata`, selected local `modelKey` | Uses the batch metadata endpoint only |
| Scope | `indexScope` / `scope` | Resolves selected, catalog, missing, or indexed photos through existing selection logic |
| Keep or replace StyleAI data | `regenerateMetadata`; replacement forces `appendMetadata=false` | Controls reprocessing and generated-field replacement |
| Generated fields | `generateKeywords`, `generateTitle`, `generateCaption`, `generateAltText` | Controls catalog/backend metadata fields |
| Write to Lightroom / review | `saveDataToCatalog`, `enableValidation` | Controls catalog handoff and per-photo metadata review |
| Append or replace generated fields | `appendMetadata` | Preserves existing metadata or replaces only generated target fields |
| Keyword organization | hierarchy, catalog-structure, bilingual preferences | Changes generated keyword organization only |
| Context | GPS, existing keywords, folder name, photo instructions | Sent only to the selected loopback local model |

The primary Prepare Photos window remains compact. **Metadata Settings...**
opens the generated-field, catalog-writing, local-model, prompt, and context
controls in a focused secondary dialog only when metadata generation is
selected. Canceling that secondary dialog restores its entry-state values;
preferences are still written only when the primary Prepare Photos action is
confirmed.

The dialog rejects an empty task selection, metadata generation without a
local model, and metadata generation with no output fields. `replaceSS` and the
top-level keyword behavior remain compatibility-reserved pending a product
decision.

Prepare Photos deduplicates stable source IDs before creating an operation.
When every unique photo is already complete, it performs no preview/export or
backend work and shows a successful already-complete message. Live success and
failure captions derive from mutually exclusive operation-item counts plus
preflight-complete or unresolved photos.

### Learn From My Edits

Scope and `forceRetrain` (now labeled **Update previously learned examples**)
map to the existing training operation. Eligibility remains RAW/DNG-only.
Selection snapshots, bounded preflight/upload chunks, stable-source
deduplication, durable operation items, cancellation, and the single final
rebuild/activation sequence are unchanged.

### Apply My Style

| UI choice | Effective behavior |
| --- | --- |
| Scope | Uses the selection snapshot captured before the modal opens |
| Style strength | Interpolates current settings toward learned absolute targets |
| Profile / HDR Off, Suggest, Auto | Preserves separate evidence and compatibility gates |
| Create virtual copies | Applies to copies in a uniquely named `StyleAI <YYMMDD-HHMMSS>` collection when enabled; defaults on unless an existing preference says otherwise. The completion dialog names the collection without making it Lightroom's active source. |
| Review each proposed edit | Opens the existing per-photo review flow; defaults on |
| Allow crop / straighten | Separate opt-in controls; both default off. Enabling either permits, but does not force, that action: independently learned crop/rotation applicability must reach the conservative inference gate. |
| Burst coherence | Automatic and conservative; no permanent preference. Bounded ordered batches may show “Coherent burst edit” after temporal, visual, EXIF/source, policy, and rendering-partition checks. Every photo retains per-photo review, geometry/rendering decisions, history, receipts, and readback. Unsafe members fall back independently. |

The trained workflow requires the local service and vision model, but not an
Ollama or LM Studio metadata provider. Inference history, application events,
idempotent full-strength behavior, clamps, and catalog write transactions are
unchanged.

The production service kill switch `STYLEAI_EDIT_BURST_COHERENCE=0` restores
the independent path without changing stored preferences or schemas. The
stricter `global_target_reuse` implementation is disabled by default and can be
enabled only for developer evaluation with
`STYLEAI_EDIT_BURST_EXACT_REUSE=1`.

### Review and maintenance

- Metadata review preserves keyword selection, edited generated text,
  de-cluttered comparison, save/discard behavior, and catalog writes.
- Edit review preserves the global-edit choice and exposes recipe details as
  read-only bounded content.
- Edit outcome labels never Undo Develop settings and remain evaluation-only.
- Find More Training Examples resolves recommendations before constructing its
  window. Empty results use a compact informational state; populated results
  retain filtering, policy details, Library selection, and all three
  evaluation-only feedback labels.
- Rebuild creates and validates a replacement generation; the active generation
  remains available unless activation succeeds.
- Training deletion removes saved training examples and learned styles only.
- Database backup/restore does not back up or restore Lightroom catalogs,
  photo files, or Develop edits.

### Plugin Manager

- Status & Setup reports the background service, required vision model, and
  optional local metadata provider without relying on color alone.
- Repair Background Service starts an offline service or restarts a reachable
  one; it does not delete catalog-local data.
- The Styles summary reports factual saved-example and active-style counts.
  Training deletion is available only in Styles & Training maintenance.
- Data & Recovery retains validated backup export, same-catalog restore with
  rollback, and removed-photo cleanup with a pre-cleanup backup.
- Database statistics remain available to diagnostics and tests but are not a
  user-facing Plugin Manager action.
- Preview acquisition is automatic and falls back to Lightroom export after
  failures or repeated timeouts.
- Processing-load overrides are Debug-only and map to automatic,
  lower-resource, or faster operation. Maximum is no longer a user-facing
  choice.
- Support reports contain system details and available StyleAI/provider logs,
  never the Lightroom catalog or original photos.

## Debug capture contract

Diagnostic LLM image capture is authorized only when both `debugMode` and
`captureLlmInputs` are true. Both default false. The plugin transmits both gates
and the backend ignores a legacy audit flag without `diagnostic_mode`.

The destination is created lazily on first authorized capture. Defaults use the
platform support/state directory, retention is bounded to 50 request groups and
512 MiB, and Clear removes only recognized StyleAI capture groups. Disabling
Debug immediately disables effective capture. The former LM Studio cache write
has been removed.

## Preference cleanup classification

| Preference / state | Classification | Treatment |
| --- | --- | --- |
| `debugMode`, `captureLlmInputs`, `captureLlmInputsPath` | Active | New double-gated Debug UI |
| `auditLlmInputs`, `auditLlmInputsPath` | Migration-only | Never migrate enabled state; retain old path once |
| `usePreviewThumbnails` | Compatibility-reserved, ignored by current UI orchestration | Preview acquisition is automatic with export fallback |
| `exportSize`, `exportQuality` | Compatibility-reserved, currently unused | Removed from Plugin Manager state; defaults retained to avoid upgrade churn |
| `indexingBatchSize` | Compatibility-reserved, currently unused by UI orchestration | Removed from Plugin Manager state and Prepare writes |
| `forceFreshPreviews` | Ineffective | Removed from the UI; existing stored value is ignored |
| legacy adjustment-group and composition-mode UI preferences | Ineffective for the retained predictive form | Removed from dialog state; supported request fields remain visible |
| `replaceSS` | Hidden-active / unresolved | Behavior preserved; no new control added |
| `useTopLevelKeyword`, `topLevelKeyword` | Active compatibility behavior / unresolved | Behavior preserved pending product direction |

## Layout exceptions

Most containers, long text, and editors use horizontal/vertical fill and all
content-heavy dialogs are resizable. Workflow dialogs begin at a bounded
reading width (normally 620 points, with wider list/editor workspaces at
660–740 points) and may expand when resized. This explicit initial width is
required because Lightroom calculates modal geometry from each child's
unwrapped intrinsic width; `wrap` plus `fill_horizontal` alone does not bound a
long localized sentence or popup menu.

Supporting descriptions use Lightroom's regular system text by default rather
than forcing the small caption style. Native controls still inherit the host OS
and Lightroom text metrics. Explicit dimensions remain only for:

- bounded initial dialog reading widths and popup menus;
- bounded photo previews;
- bounded prompt, log, recipe, keyword, and metadata scrollers;
- compact sliders and numeric values;
- learned-style `simple_list`, because Lightroom's mixed
  control width sharing is unreliable across platforms.

These exceptions require visual verification in Lightroom on macOS Tahoe or
newer and Windows scaling configurations before release.
