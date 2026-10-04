# Style Refinement Implementation Checklist

Status: QR-1/CV-1 evidence infrastructure and a developer Lightroom preview
spike implemented on August 27, 2026; human preview gates and candidate-model
phases remain. This is the source of truth for the
optional **Refine Styles...** workflow, including **Quick Refinement**,
**Correction Validation**, preference evidence, inactive candidate models,
comparison, activation, and rollback. It depends on the shared reporting and
quality gates in `APPLIED_EDIT_QUALITY_UI_IMPLEMENTATION_CHECKLIST.md`.

No current production behavior is changed by this plan. Existing ratings and
training examples remain evaluation/base-training evidence until the phases
below are implemented and validated.

## Implementation progress

- Implemented ordered migration `014_style_refinement_evidence` with separate
  sessions, immutable blinded pairs, append-only choices, candidate manifests,
  and activation/rollback lineage tables. No image bytes or source paths are
  stored.
- Implemented bounded backend session creation, active-generation pinning,
  deterministic A/B assignment, immediate idempotent choice persistence,
  corrected-choice append behavior, final summaries, and standard API
  envelopes.
- Quick Refinement QR-1 uses only conservative 70% versus 100% alternatives
  along the already learned target direction. It does not jitter independent
  sliders or fit/mutate a model.
- Correction Validation CV-1 requires the latest explicit
  `modified_and_kept` outcome and a fresh matching Lightroom modeled-slider
  readback before admitting the recorded correction.
- Added **Developer: Test Style Refinement...**. The spike requires two
  caller-prepared disposable virtual copies per inference, renders blinded
  alternatives side by side, restores both copies after each comparison, and
  records evidence only.
- QR-2/QR-3 and CV-2/CV-3 remain deliberately unimplemented until the Lightroom
  fidelity, restoration, shutdown, and evidence gates pass. No candidate can
  currently be built or activated from these choices.

## Product outcome

- [ ] Give photographers an optional, resumable way to refine learned editing
      behavior by choosing between blinded visual alternatives.
- [ ] Support two coordinated modes:
  - **Quick Refinement** generates controlled alternatives for diverse,
    informative photos and does not require prior manual corrections.
  - **Correction Validation** tests whether an inactive candidate has learned
    patterns from explicit **Modified and Kept** outcomes on evidence it did
    not train on.
- [ ] Preserve manually authored **Learn From My Edits** examples as immutable
      base anchors. Never overwrite or silently relabel them with generated
      preference evidence.
- [ ] Store preference evidence separately and make its influence removable.
      Deleting refinement evidence and rebuilding must recover the behavior
      implied by the retained base training set.
- [ ] Build complete inactive candidates first. A refinement session must never
      mutate active artifacts, thresholds, policy identity, or training rows.
- [ ] Require explicit candidate activation and retain the preceding active
      generation for rollback. Automatic activation is outside the initial
      product scope.
- [ ] Keep the workflow completely catalog-local and loopback-only. Do not add
      telemetry, cloud models, image upload, external accounts, or API keys.
- [ ] Do not require Refine Styles for applying edits, training, rebuilding,
      recommendations, backup/restore, or normal catalog use.

## Implementation order

1. Complete the shared applied-edit reporting service and developer assessment
   UI from `APPLIED_EDIT_QUALITY_UI_IMPLEMENTATION_CHECKLIST.md`.
2. Add refinement schemas, session persistence, authoritative eligibility, and
   controlled variant generation with no model mutation.
3. Prove blinded Lightroom preview fidelity, cleanup, cancellation, resume, and
   privacy through the developer-only QR-1/CV-1 workflows.
4. Fit and evaluate a broad inactive Quick Refinement residual using synthetic
   recovery and untouched manual holdouts.
5. Add cross-fitted Correction Validation and confirm that corrected patterns
   generalize beyond the photos that supplied them.
6. Combine eligible evidence under one versioned weighting contract, implement
   atomic activation/rollback, and keep it developer-only through independent
   catalog gates.
7. Promote Refine Styles to an optional production workflow only after the
   rollout gates pass; retain explicit activation in the first release.

## Evidence terminology and strength

- [ ] Define and version the following evidence classes:
  - `manual_training_target`: a final edit saved through Learn From My Edits;
    strongest absolute-target evidence.
  - `modified_and_kept_target`: a fresh modeled-slider readback explicitly
    marked Modified and Kept; strong correction evidence.
  - `keepable_variant_target`: a generated variant explicitly marked “I would
    keep this”; soft absolute-target evidence.
  - `pairwise_preference`: A preferred over B; relative utility evidence only.
  - `equivalent_pair`: both acceptable or no meaningful difference; tolerance
    and calibration evidence.
  - `neither_pair`: neither satisfactory; negative suitability evidence, never
    a numeric target.
  - `skipped_pair`: no learning signal.
- [ ] Preserve accepted-unchanged and rejected edit outcomes as evaluation and
      routing/confidence evidence. Do not copy an unchanged model prediction
      back into training as synthetic human truth.
- [ ] Keep rejected and neither-satisfactory outcomes out of numeric regression
      targets because they do not identify a correct recipe.
- [ ] Establish conservative, versioned weights with manual targets strongest,
      corrected readbacks next, keepable generated targets lower, and pairwise
      comparisons affecting only the preference objective.
- [ ] Store the evidence-weighting contract in candidate manifests and reports.

## Eligibility and authoritative state

- [ ] Use Lightroom's StyleAI Applied/State/Review metadata for discoverability
      and user explanation only. Revalidate every candidate against catalog-
      local inference/event history before admitting it.
- [ ] For Correction Validation, require the latest explicit outcome to be
      `modified_and_kept`, an applied inference, compatible source/rendering
      provenance, and a current modeled-state fingerprint matching the recorded
      correction.
- [ ] Do not treat reconciled `diverged` or visible **Modified** metadata as a
      preference without a corresponding explicit Modified-and-Kept action.
- [ ] For Quick Refinement, admit indexed catalog photos with compatible
      target-independent source evidence and a valid active policy prediction;
      prior application or review is optional.
- [ ] Exclude panoramas, stale or incomplete canonical vectors, incompatible
      rendering partitions, missing photos, rejected sources when the proposed
      comparison cannot resolve the rejection, and photos currently involved
      in conflicting operations.
- [ ] Exclude existing manual training examples from ordinary Quick Refinement
      because their final human edits already provide stronger evidence. Permit
      them only in a deliberate validation fixture that never trains on its own
      comparison target.
- [ ] Deduplicate stable source identities, virtual copies, and burst members.
      Weight any admitted burst evidence by source/burst rather than instance.
- [ ] Detect stale Lightroom state before rendering each pair. Skip or request
      a fresh review rather than comparing against a rendition different from
      the recorded preference.

## Candidate selection and active learning

- [ ] Build a bounded, diverse queue rather than scanning or presenting the
      entire catalog in one response.
- [ ] Rank already-eligible Quick Refinement photos by information value using
      model uncertainty, weak policy coverage, plausible residual disagreement,
      profile/partition diversity, and distance from previously compared
      evidence.
- [ ] Use source evidence and policy membership for admission; use ratings,
      pick status, and coverage only to rank admitted candidates.
- [ ] Preserve policy precision as the gate. Do not blend competing policies
      merely to create visually different alternatives.
- [ ] Select deterministic multi-medoid or bounded diversity representatives
      and avoid full catalog `N x N` similarity matrices.
- [ ] Limit repeated examples from one policy, camera, profile, subject cluster,
      or capture session so an easy high-volume region cannot dominate.
- [ ] Record why each photo was selected and every exclusion reason in bounded,
      non-sensitive diagnostics.
- [ ] After each answer, permit the backend to reprioritize remaining candidates
      or narrow a controlled variant direction, but never force a second pair
      for the same photo.

## Controlled variant generation

- [ ] Version a refinement-variant schema containing the active generation,
      policy, hard partition, source stamp, baseline target, variant target,
      modeled keys, generation method, safe bounds, and recipe fingerprint.
- [ ] Generate variants only from plausible learned directions:
  - uncertainty-bounded alternatives around the active target;
  - low-rank correction directions learned from held-out residuals;
  - validated Modified-and-Kept correction patterns;
  - conservative style-strength alternatives represented as absolute targets;
  - outputs from complete inactive candidate generations.
- [ ] Do not use arbitrary independent slider jitter. Coordinated variants must
      remain on evidence-supported target manifolds and inside Lightroom-safe
      and learned target bounds.
- [ ] Start with continuous global modeled sliders. Exclude profile, HDR,
      categorical white balance, crop, rotation, masks, and other structural or
      sparse families from the first release.
- [ ] Add each excluded family only through a separate applicability,
      reproducibility, preview-fidelity, and held-out validation gate.
- [ ] Keep unmodeled Lightroom settings identical between A and B so the choice
      isolates the candidate's modeled differences.
- [ ] Ensure alternatives are meaningfully different but not trivially extreme.
      Define lower and upper perceptual/normalized target-distance gates and
      return no pair when a useful controlled contrast cannot be generated.
- [ ] Randomize A/B side assignment per immutable pair and store the assignment
      separately from the recipe identities.
- [ ] Optionally repeat a small, explicitly bounded subset in developer studies
      to estimate choice consistency; do not burden normal users with hidden
      repeated pairs in the initial product.

## Preference model and refined target contract

- [ ] Begin with a small, regularized preference-residual layer around the
      active base policy rather than refitting unrestricted policies from
      generated alternatives.
- [ ] Fit pairwise utility from source evidence plus the difference between two
      candidate recipes. A choice constrains relative utility; it is not an
      absolute target unless the user also marks the winner keepable.
- [ ] Learn broad per-policy residual directions before source-conditioned or
      local refinement. Add contextual refinement only when burst-preserving
      held-out evidence shows stable material improvement.
- [ ] Condition only on target-independent source evidence and the immutable
      policy/rendering partition. Current Develop sliders must never become
      model features.
- [ ] Combine the predicted residual with the base absolute target, then apply
      existing clamps, missing-target defaults, rendering rules, and strength
      semantics.
- [ ] Canonically order every component for deterministic artifacts and
      fingerprints.
- [ ] Store preference models as versioned artifacts inside the inactive
      generation. Do not create an opaque mutable sidecar outside generation
      activation and backup contracts.
- [ ] Abstain when comparison evidence is sparse, contradictory, distant, or
      high variance. The base policy prediction remains the fallback.
- [ ] Provide a candidate manifest that reports evidence counts by class,
      policy, partition, source/burst weight, and exclusion reason.

## Leakage prevention and cross-fitting

- [ ] Split all candidate building and evaluation by stable source and burst so
      originals, virtual copies, and related frames cannot cross folds.
- [ ] Never present a candidate prediction as validation evidence when that
      candidate or residual component trained on the same photo or burst.
- [ ] Use cross-fitted candidate predictions for Correction Validation when
      evidence is too limited to reserve a permanent holdout.
- [ ] Keep pair generation, preference fitting, candidate selection, and final
      activation evaluation seeds deterministic and recorded.
- [ ] Separate exploratory Quick Refinement choices used to fit a candidate
      from confirmatory blinded comparisons used to promote it.
- [ ] Preserve labelled synthetic recovery, ambiguity, admission, and
      cross-policy leakage fixtures from the base editing architecture.

## Catalog-local persistence and migration

- [ ] Add an ordered styles SQLite migration for append-only refinement state.
- [ ] Add versioned tables or equivalent normalized storage for:
  - refinement sessions and their base/candidate generations;
  - immutable blinded pairs and A/B assignment;
  - append-only user choices and corrections;
  - candidate build/evaluation provenance;
  - activation decision and rollback lineage.
- [ ] Store complete numeric recipe targets and fingerprints, but never image
      bytes, exported previews, absolute source paths, or private textual photo
      metadata in refinement tables.
- [ ] Reference canonical source embeddings through validated provenance rather
      than copying them into the preference tables or mixing Chroma collections.
- [ ] Preserve session and preference evidence across derived-generation
      pruning. Delete it only through an explicit refinement-data action.
- [ ] Include refinement tables and candidate artifacts in existing backup,
      restore, integrity, ownership-marker, and destructive-reset contracts.
- [ ] Add **Delete Refinement Evidence...** in Styles & Training only after a
      pre-mutation backup and explicit explanation that base training examples
      remain intact.
- [ ] Make interrupted session choices resumable. Each answer must commit
      incrementally; **Save and Exit** finalizes the session rather than being
      the only persistence point.

## Backend services, jobs, and HTTP boundary

- [ ] Add `style_refinement` and `refinement_candidate` operation kinds with
      scoped cancellation and conservative interrupted-job recovery.
- [ ] Keep route modules limited to request validation and response envelopes;
      put admission, selection, variant generation, fitting, and evaluation in
      dedicated services.
- [ ] Add bounded endpoints for session preflight/create, next pair, pair choice,
      session summary/finalize, candidate build/status, candidate comparison,
      activation, rollback, and explicit evidence deletion.
- [ ] Use opaque session/pair identifiers and idempotency keys. Retrying a
      choice must not append duplicates; correcting a choice must append a new
      event rather than overwrite history.
- [ ] Return the standard `results`/`error`/`warning` envelope and bounded
      summaries. Do not return full histories, embeddings, or artifacts to Lua.
- [ ] Acquire complete resource vectors atomically. Candidate inference/fitting
      may use CPU and accelerator lanes; preference collection itself must not
      claim the LLM lane.
- [ ] Use the workflow/maintenance gate so restore, destructive reset, training
      deletion, and generation activation cannot race refinement commits.
- [ ] Pin every refinement session and pair to one base generation. If the
      active generation changes, pause the session and require an explicit
      restart/rebase decision.
- [ ] Build and validate a complete inactive generation before atomically
      registering it as eligible for user comparison.
- [ ] Keep cancellation responsive between bounded history pages, candidate
      batches, estimator fits, report phases, and artifact commits.
- [ ] Log aggregate timings, counts, and bounded reason codes without photo IDs,
      Develop values, paths, embeddings, pair recipes, or preview contents at
      INFO.

## Lightroom preview pipeline

- [ ] Implement a developer spike in a disposable catalog before committing to
      the production preview architecture.
- [ ] Never apply a refinement variant to the user's original or retained
      preferred virtual copy.
- [ ] Render A and B from two equivalent disposable virtual-copy states or an
      SDK mechanism proven to preserve identical unmodeled settings.
- [ ] Apply absolute modeled targets at full strength to isolate target
      differences. Preserve the normal `current + strength * (target-current)`
      contract when a strength dimension itself is under comparison.
- [ ] Export both sides with identical color space, dimensions, sharpening,
      quality, crop/orientation handling, and metadata stripping.
- [ ] Fingerprint the rendered files and associate them with the immutable pair,
      but keep the files only in a session-scoped temporary directory.
- [ ] Present both previews at equal size with synchronized fit/zoom behavior
      where the Lightroom SDK permits it. Do not reveal which side is active,
      corrected, or candidate.
- [ ] Validate preview fidelity for tone, color, detail, crop boundaries, SDR,
      and supported displays before treating choices as training evidence.
- [ ] Verify whether Lightroom can safely remove disposable virtual copies. If
      not, reuse a bounded refinement-preview collection/copy pool and provide
      deterministic cleanup without altering unrelated collection membership.
- [ ] Clean temporary exports and disposable copies after each pair or session,
      with startup recovery for interrupted cleanup.
- [ ] Yield outside catalog transactions and keep all catalog mutations in
      bounded non-yielding write transactions coordinated through
      `WorkCoordinator`.

## Lightroom user experience

- [ ] Prototype under **Help > Plug-in Extras → Developer: Test Style
      Refinement...** until statistical, preview, cleanup, and model gates pass.
- [ ] After release approval, expose **Refine Styles...** as an optional normal
      photography workflow and from **Styles & Training**. Keep developer
      diagnostics under Help.
- [ ] Start with a mode choice:
  - **Quick Refinement — compare new StyleAI variations**;
  - **Correction Validation — test whether StyleAI learned your corrections**.
- [ ] Explain eligible-photo counts, why some photos are unavailable, the
      current base generation, whether a candidate must be built, and that the
      active style will not change during the session.
- [ ] Offer bounded session goals such as 5, 10, or Continue until I stop without
      implying that a fixed count guarantees improvement.
- [ ] For every pair provide **Prefer A**, **Prefer B**, **Both acceptable / no
      meaningful difference**, **Neither is satisfactory**, and **Skip**.
- [ ] After choosing A or B, optionally ask **Would you keep this result?** only
      when the extra absolute-target signal materially improves the model. Do
      not turn every pair into a two-dialog burden.
- [ ] Persist the choice immediately, update session progress, and present the
      next pair without unnecessary model reload or catalog focus changes.
- [ ] Let the user pause, resume, cancel pending work, or **Save and Exit** at any
      point. Already answered pairs must not reappear unless explicitly selected
      for a corrected choice.
- [ ] On finalization, show choices saved, skipped/equivalent/neither counts,
      evidence sufficiency, candidate-build state, and the next action in plain
      language.
- [ ] Show **Candidate improved**, **No meaningful difference**, **Candidate
      worse**, or **More comparisons needed** rather than requiring statistical
      interpretation.
- [ ] Offer **Use Refined Styles** only when all activation gates pass. Show the
      prior generation and an explicit rollback explanation.
- [ ] Keep **Reset to Previous Style Generation** and refinement-evidence
      deletion distinct: rollback changes the active derived generation;
      deletion removes preference evidence after backup.
- [ ] Localize all strings in English, Catalan, German, Spanish, and French.

## Candidate evaluation and activation gates

- [ ] Extend the shared applied-edit evaluator or add a versioned refinement
      evaluator without weakening the existing v2 report contract.
- [ ] Report at minimum:
  - pairwise win/loss/equivalent/neither/skip counts;
  - burst/source-weighted preference rate and Wilson interval;
  - evidence coverage by policy and partition;
  - target error on untouched manual holdouts;
  - delivered-target corrections by target family;
  - confidence calibration and selective coverage;
  - cross-policy/partition leakage and ambiguity abstention;
  - accepted-unchanged regression rate;
  - candidate fallback/abstention rate.
- [ ] Establish minimum independent evidence and uncertainty requirements before
      comparing or activating candidates. Treat the existing 30 reviewed edits
      per generation as a reporting baseline, not proof that every preference
      component is adequately supported.
- [ ] Require material held-out improvement over the active generation and no
      unacceptable regression in high-confidence accepted edits, rendering
      compatibility, structural families, or sparse policies.
- [ ] Require confirmatory preference comparisons that were not used to fit the
      candidate. Exploratory pair wins alone cannot activate a generation.
- [ ] Fail closed when evidence is insufficient, contradictory, or concentrated
      in one source/burst/policy region.
- [ ] Store the complete evaluation report and its fingerprint before marking a
      candidate activation-eligible.
- [ ] Activate atomically only after verifying every row/artifact and current
      base-generation lineage. A failed activation must leave the active model
      unchanged.
- [ ] Keep one-click rollback to the immediately prior verified generation and
      retain immutable activation/rollback events.

## Quick Refinement staged delivery

### QR-1: Evidence-free variant prototype

- [ ] Generate safe pairs from the active model without fitting a preference
      model or changing training data.
- [ ] Validate pair diversity, plausibility, perceptual distance, blinding,
      rendering, cleanup, cancellation, and session persistence.
- [ ] Use the resulting choices for reports only.

Exit gate: users can make reliable, resumable blinded choices without catalog
side effects or any model mutation.

### QR-2: Global/per-policy preference residual

- [ ] Fit a strongly regularized broad residual from exploratory pairwise and
      keepable-winner evidence, with source/burst-safe weighting.
- [ ] Produce a complete inactive candidate and cross-fitted predictions.
- [ ] Compare against untouched manual targets and confirmatory preference
      pairs.

Exit gate: synthetic fixtures recover known preferences, irrelevant choices do
not move targets, and sparse policies abstain to the base prediction.

### QR-3: Conditional refinement

- [ ] Add source-conditioned residual behavior only when broad refinement leaves
      a stable, repeatable held-out opportunity.
- [ ] Bound any local refinement by the production distance, neighbor-count,
      variance, and abstention contracts.

Exit gate: conditional refinement materially improves held-out evidence without
cross-policy leakage or degradation outside its admitted region.

## Correction Validation staged delivery

### CV-1: Authoritative corrected-evidence cohort

- [ ] Build a bounded cohort from explicit Modified-and-Kept events, validate
      current readback fingerprints, and report every exclusion reason.
- [ ] Group by base generation, policy, rendering partition, target family, and
      burst-safe source identity.

Exit gate: no mere divergence, stale correction, duplicate instance, rejected
edit, or missing source is mislabeled as a correction target.

### CV-2: Cross-fitted correction candidate

- [ ] Fit correction residuals using folds that exclude each comparison photo
      and its burst relatives.
- [ ] Render corrected A versus cross-fitted candidate B with randomized sides.
- [ ] Interpret corrected preference as strong target evidence and candidate
      preference as generalization evidence; neither/equivalent remain distinct.

Exit gate: candidate wins cannot be explained by training on the same corrected
photograph.

### CV-3: Candidate promotion

- [ ] Combine eligible Quick Refinement and Correction Validation evidence under
      the versioned weighting contract.
- [ ] Run full held-out, recovery, leakage, calibration, and blinded preference
      gates before making the inactive generation activation-eligible.

Exit gate: the candidate improves unseen evidence and retains the complete base
model safety contract.

## Automated tests

- [ ] Add schema/migration/backup/restore/integrity tests for every refinement
      table and artifact.
- [ ] Add deterministic synthetic preference fixtures with known global,
      per-policy, conditional, contradictory, sparse, and drifting preferences.
- [ ] Prove pairwise choices recover the correct direction without treating
      rejected/neither outcomes as numeric targets.
- [ ] Prove manual training targets remain unchanged and dominant under sparse
      generated preference evidence.
- [ ] Add source/burst/virtual-copy leakage tests across fitting, cross-fitting,
      validation, and reporting.
- [ ] Add variant-bound, perceptual-distance, clamp, unsupported-family, and
      deterministic-randomization tests.
- [ ] Add session idempotency, corrected-choice append, resume, cancel,
      interrupted recovery, and active-generation-change tests.
- [ ] Add route validation, standard-envelope, bounded-response, privacy, and
      malformed-request tests.
- [ ] Add operation admission and maintenance-race tests covering training,
      Apply My Style, restore, activation, and refinement running concurrently.
- [ ] Add candidate completeness, failed-build isolation, atomic activation,
      rollback, pruning, and base-lineage tests.
- [ ] Add evaluator tests for pairwise intervals, weighted source counts,
      evidence sufficiency, accepted-edit regressions, and report fingerprints.
- [ ] Add Lua/static tests for developer/production menu placement, dialog
      choices, immediate persistence, resume, progress cancellation, source
      immutability, collection isolation, and preview cleanup.
- [ ] Cross multiple keyset pages and large session queues without fixed catalog
      ceilings, unbounded queues, or full similarity matrices.

## Lightroom human gates

- [ ] In a disposable catalog, verify A/B side randomization and confirm logs,
      metadata, and summaries never reveal the side before choice.
- [ ] Confirm current preferred photos, source originals, manual training
      examples, collection memberships, and Develop histories are unchanged.
- [ ] Confirm previews match Develop at practical inspection sizes and that
      both sides use identical render settings.
- [ ] Exercise Prefer A/B, equivalent, neither, skip, corrected choice, cancel,
      Save and Exit, crash/restart resume, and candidate-build failure.
- [ ] Run Quick Refinement with no Modified-and-Kept evidence and confirm it can
      still create useful controlled comparisons.
- [ ] Run Correction Validation with stale, current, duplicate, burst-related,
      and cross-generation edits and confirm only valid evidence is admitted.
- [ ] Compare cross-fitted candidate previews against corrected images and
      verify no photo was used in its own candidate fold.
- [ ] Activate a passing candidate, apply it to an untouched representative
      set, verify idempotence and safety, then rollback and reproduce the prior
      results.
- [ ] Quit Lightroom during pair rendering and candidate building; shutdown
      must remain prompt without a plug-in shutdown hook.
- [ ] Restore a backup around refinement sessions and candidates and verify
      ownership, integrity, session resume, artifact availability, and active
      generation lineage.
- [ ] Test under memory pressure and verify candidate fitting/preview work yields
      to runtime pressure without loading an LLM or multiplying accelerator use.

## Documentation, release, and rerun instructions

- [ ] Update `AGENTS.md`, Architecture, UI behavior contracts, Plugin Guide,
      Developer Guide, Getting Started, troubleshooting, and the human test
      matrix when behavior becomes reachable.
- [ ] Clearly document that ratings and refinement remain local and optional,
      and that no user evidence is available to developers without explicit
      export/sharing.
- [ ] Document preference-evidence deletion, candidate activation, rollback,
      report interpretation, and the distinction between training and
      refinement evidence.
- [ ] Keep the CLI evaluator and Lightroom developer report consistent with the
      refinement report schema and candidate lineage.
- [ ] Run translation synchronization, plug-in validation, lint/format, focused
      tests, the complete backend suite, schema validation, and disposable
      release packaging at every reachable release phase.
- [ ] Existing catalogs should require no **Prepare Photos**, **Learn From My
      Edits**, or **Styles & Training → Rebuild** merely to expose the workflow.
      Refine Styles itself builds derived inactive candidates from compatible
      existing evidence. If a future implementation changes source evidence,
      grouping, EXIF, schema, or base learned-model contracts, document the
      exact required rerun for that change before release.

## Rollout decision gates

- [ ] Keep QR-1 and CV-1 developer-only until preview fidelity, cleanup,
      cancellation, privacy, and session recovery pass in Lightroom.
- [ ] Keep candidate fitting developer-only until synthetic recovery and
      untouched held-out catalog evidence demonstrate material improvement.
- [ ] Keep activation developer-only until at least two independent catalog
      evaluations validate atomic promotion and rollback without regressions.
- [ ] Expose Refine Styles as an optional production workflow only after its
      benefits, time cost, evidence requirements, and failure states can be
      explained without developer terminology.
- [ ] Do not implement silent automatic activation in the initial production
      release. Reconsider it only after sufficient longitudinal evidence proves
      the activation gates and rollback behavior reliable.

## Completion criteria

- [ ] Quick Refinement can improve an inactive candidate from controlled,
      blinded preferences without requiring manual corrections.
- [ ] Correction Validation can prove whether corrected preference patterns
      generalize without training/evaluation leakage.
- [ ] Original training examples remain immutable and preference influence is
      separately inspectable, deletable, backed up, and reproducible.
- [ ] Every candidate is complete, versioned, catalog-local, safely bounded,
      evaluated against untouched evidence, explicitly activated, and
      rollback-capable.
- [ ] Lightroom remains responsive, source photos remain unchanged, sessions
      resume safely, and ordinary users are never required to refine or
      interpret developer metrics.
