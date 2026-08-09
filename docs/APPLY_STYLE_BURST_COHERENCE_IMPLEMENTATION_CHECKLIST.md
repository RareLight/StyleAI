# Apply My Style Burst-Coherence Implementation Checklist

Status: implementation and release validation in progress. Conservative
operation-scoped batching and `policy_coherent` classification are reachable
production behavior. `global_target_reuse` is implemented behind the internal
`STYLEAI_EDIT_BURST_EXACT_REUSE=1` evaluation gate and remains off by default
until the unchecked held-out and human Lightroom gates pass. Current behavior
remains defined by `UI_BEHAVIOR_CONTRACTS.md`, `AGENTS.md`, and production code.

## Goal and product boundaries

- [ ] Make edits across a true photographic burst more consistent while
  avoiding redundant policy work.
- [ ] Optimize for coherent results first; treat reduced inference time as a
  secondary benefit because Apply My Style does not use an LLM.
- [x] Limit grouping and reuse to one catalog-local Apply My Style operation.
  Do not add cross-catalog routing, shared databases, cloud services, or
  durable cross-run recipe caches.
- [x] Require every member photo to retain its own target-independent source
  evidence, operation item, immutable inference, Lightroom application event,
  and exact Develop readback.
- [x] Never infer burst membership from filenames, sequence numbers, photo IDs,
  keywords, genre labels, or vector presence alone.
- [x] Never copy a representative's complete recipe without independently
  validating the member photo and filtering the recipe by reuse tier.
- [x] Keep profile, HDR, crop, rotation, and masks photo-specific in the first
  production version.
- [x] Preserve current strength semantics: shared absolute targets still apply
  as `current + strength * (target - current)` for each member independently.
- [x] Preserve per-photo review, virtual-copy collection placement, scoped
  cancellation, error accounting, and application receipts.
- [x] Do not add a permanent user preference unless human testing demonstrates
  that an automatic conservative optimization is insufficiently transparent.

## Tier contract

- [x] Define and version a burst-edit grouping schema and reuse-policy schema.
- [x] Use three explicit outcomes for every photo:
  - `independent`: ordinary independent policy selection and prediction.
  - `policy_coherent`: the representative policy may be used as a candidate,
    but the member uses its own source features and receives its own absolute
    target prediction.
  - `global_target_reuse`: a strictly admitted member may reuse an allowlisted
    subset of the representative's absolute global target.
- [x] Treat the representative as an optimization anchor, not as ground truth.
  A member that fails any gate must fall back to independent inference.
- [x] Define a conservative initial exact-reuse allowlist covering continuous
  global tone, presence-safe color, and detail targets only.
- [x] Exclude white-balance mode and its numeric targets from exact reuse until
  member-level agreement and held-out precision are validated.
- [x] Exclude sparse or structural families from exact reuse until each has a
  validated applicability contract.
- [x] Remove crop coordinates and rotation angle from all reused targets even
  when the user has enabled those permissions; run their learned applicability
  gates and predictions per photo.
- [x] Run profile and HDR selectors per photo. Permit grouping only after all
  admitted members resolve to the same effective rendering partition.
- [x] Keep masks per photo; do not reuse mask geometry, selections, or local
  adjustments from a representative.
- [x] Store the tier chosen for every photo and the reason for every fallback.

Exit gate: a written contract identifies every reusable and non-reusable target
family and has no ambiguous "copy the edit" behavior.

## Phase 1: Baseline, fixtures, and observability

- [ ] Capture baseline Apply My Style timings for cached and uncached source
  embeddings across singles, true bursts, near-bursts, and unrelated photos.
- [ ] Record baseline per-photo policy IDs, confidence, entropy, absolute
  targets, applied recipes, and Lightroom readback for deterministic fixtures.
- [ ] Add labelled fixtures for sports bursts, panning bursts, exposure
  brackets, lighting transitions, subject entrances/exits, camera switches,
  crop changes, and visually similar non-burst photos.
- [ ] Include fixtures where temporal proximity passes but visual similarity
  fails, and where visual similarity passes but temporal proximity fails.
- [ ] Include cross-policy and ambiguous-membership fixtures so grouping cannot
  hide policy leakage.
- [ ] Define baseline metrics for target error, selective coverage, policy
  agreement, reuse precision, fallback rate, crop/rotation disagreement,
  cross-policy leakage, latency, and avoided policy predictions.
- [x] Log only bounded provenance and timings at INFO. Keep paths, pixels,
  embeddings, Develop settings, and sensitive metadata out of INFO logs.

Exit gate: independent inference provides a reproducible oracle against which
every proposed reuse decision can be compared.

## Phase 2: Bounded burst-candidate construction

- [x] Add a backend service for burst candidate construction; keep HTTP request
  parsing in routes and business rules in the service.
- [x] Reuse the established maximum candidate window of capture delta `<=10 s`
  and SigLIP2 cosine distance `<=0.05` as ceilings, not automatic reuse gates.
- [x] Compare only temporal neighbors inside the bounded window. Do not build a
  full catalog or large-group `N x N` similarity matrix.
- [x] Require complete compatible canonical embedding stamps. If a member's
  source fingerprint, provenance, model, preprocessing, or schema stamp is
  missing or stale, recompute it or fall back independently.
- [x] Prefer target-independent embedded RAW previews. Do not admit rendered
  Lightroom proxies to exact global-target reuse.
- [x] Retain each member's own source metrics and use them in tier admission.
- [x] Require compatible camera/profile/HDR state and reject panoramas or other
  true rendering incompatibilities before forming a reusable group.
- [x] Detect and reject likely exposure brackets using EXIF and source-exposure
  deltas rather than capture time alone.
- [x] Add bounded checks for ISO, aperture, shutter speed, focal length, lens,
  camera identity, and source-exposure metrics where available. Treat these as
  compatibility evidence, not style identities.
- [x] Split oversized temporal groups into bounded deterministic windows.
- [x] Select a deterministic visual medoid or bounded approximate medoid as the
  representative; do not use rating, pick status, or current edit complexity
  to decide the inference anchor.
- [x] Give every group and member a deterministic operation-scoped identifier.
- [x] Return explicit diagnostics for candidate count, accepted group count,
  group sizes, distance distributions, and rejection reasons.

Exit gate: no unrelated, bracketed, incompatible, or stale-evidence photo is
admitted merely because it was captured near another photo.

## Phase 3: Similarity tiers and conservative admission

- [x] Evaluate at least a strict and moderate candidate tier inside the
  established `10 s` / `0.05` ceilings.
- [ ] Derive tier thresholds from burst-preserving held-out fixtures. Do not
  tune thresholds from production outcomes automatically.
- [x] Require a stricter visual and temporal threshold for
  `global_target_reuse` than for `policy_coherent`.
- [x] Require source-exposure and color-evidence agreement for exact global
  reuse; embedding distance alone is insufficient.
- [x] Independently assign each member against the representative's policy
  anchors. Reject ambiguous assignments and confidence below the validated
  admission gate.
- [x] Reject a member when its best policy differs from the representative or
  competing-policy responsibility is too high.
- [x] Require exact effective rendering-partition agreement after per-photo
  profile/HDR selection.
- [x] Make all tier decisions monotonic: stricter tiers must be subsets of
  looser tiers, and runtime pressure may reduce reuse coverage but never relax
  a safety threshold.
- [x] Version thresholds and include their versions in inference provenance and
  evaluation exports.
- [x] Fall back to independent inference on missing evidence, exceptions,
  cancellation, uncertain membership, or failed validation. Never fail the
  entire burst because one member is unsafe to reuse.

Exit gate: held-out exact-reuse precision meets the approved target with no
material increase in catastrophic target error or cross-policy leakage.

## Phase 4: Batched edit inference service

- [x] Add a bounded batch edit endpoint or extend the current endpoint with a
  versioned batch contract. Keep the standard `results`/`error`/`warning`
  envelope and a result for every requested photo.
- [x] Validate that every item belongs to the admitted catalog-local edit
  operation and reject duplicate or unexpected photo IDs.
- [x] Resolve or generate every member's own canonical source embedding and
  source metrics before grouping.
- [x] Batch embedding generation under existing accelerator, CPU-prepare,
  image-byte, queue, and pressure-governor limits.
- [x] Run the representative through ordinary production inference first; do
  not add a second model path that can diverge from independent inference.
- [x] For `policy_coherent`, validate the representative policy for the member
  and run the production calibrator/local-corrector path on that member's own
  source features.
- [x] For `global_target_reuse`, construct a member recipe by copying only the
  versioned allowlist from the representative absolute target, then merge
  member-specific profile/HDR, geometry, and other excluded decisions.
- [x] Apply target bounds and categorical/applicability gates after merging so
  reused data cannot bypass production clamps or abstention.
- [x] Persist one immutable inference per member before returning its recipe.
- [x] Ensure one member's persistence or inference failure does not corrupt or
  roll back successful independent members outside the required transaction.
- [x] Keep backend completion nonterminal until Lightroom records the required
  per-photo handoff.
- [x] Support idempotent retries without creating duplicate inferences or
  application events.

Exit gate: batch and independent requests return equivalent per-photo contracts,
and disabling reuse reproduces the existing production path.

## Phase 5: Immutable provenance and schema

- [x] Add an ordered `styles.sqlite` migration for immutable burst-reuse
  provenance; do not edit an existing migration.
- [x] Store grouping-schema version, reuse-policy version, group ID,
  representative photo ID, selected tier, capture delta, cosine distance,
  relevant source-metric deltas, policy-agreement evidence, and fallback reason.
- [x] Keep the complete member-specific generation, policy, feature schema,
  target schema, pre-edit fingerprint, modeled keys, absolute target, strength,
  confidence, and entropy already required by inference history.
- [x] Do not replace member provenance with a pointer to the representative.
- [ ] Validate migration rollback/recovery, backup compatibility, foreign-key
  integrity, JSON shape, and same-catalog ownership.
- [x] Include burst provenance in diagnostic/evaluation exports without
  exposing image content or unbounded metadata.
- [x] Update inference and evaluation schema versions where their contracts
  change.

Exit gate: every returned recipe can be independently reconstructed and audited
even if its representative or derived generation is later pruned.

## Phase 6: Lightroom client integration

- [x] Add a bounded batch client method in `APISearchIndex.lua` and retain the
  single-photo method for fallback and compatibility.
- [x] Refactor `AiEditAction.lua` producers to submit operation-scoped batches
  without creating an unbounded queue or holding all exported proxies in memory.
- [x] Preserve the selection snapshot captured before the options dialog.
- [x] Preserve source-photo order for per-photo review and application even
  when backend batches complete out of order.
- [x] Continue to create virtual copies lazily only for edits the user accepts,
  and batch their placement into the operation's StyleAI collection.
- [x] Keep review choices per photo in the first release. Do not let acceptance
  of a representative silently accept every member.
- [x] Display optional, localized review context such as "coherent burst edit"
  and group size without exposing internal thresholds as false certainty.
- [x] Keep crop, rotation, masks, profile, and HDR controls behaviorally
  identical to independent inference.
- [x] Report progress, skips, warnings, errors, and cancellation per photo, not
  per representative.
- [x] On cancellation, stop new backend work, finish required Lightroom
  handoffs for already applied edits, and finalize all operation items.
- [x] Never change Lightroom's active source to the burst group or output
  collection.
- [x] Localize every new visible string and synchronize English, Catalan,
  German, Spanish, and French resources.

Exit gate: optimization is invisible to catalog safety, selection behavior,
virtual-copy handling, review semantics, and Undo expectations.

## Phase 7: Automated tests

- [x] Unit-test bounded temporal-neighbor grouping, transitive groups,
  deterministic splitting, representative selection, and stable group IDs.
- [ ] Unit-test boundary values around every time, cosine, exposure, and policy
  admission threshold.
- [x] Test stale/mismatched embedding stamps, rendered-proxy evidence, missing
  EXIF, panoramas, brackets, camera/profile/HDR changes, and ambiguous policies.
- [x] Test that moderate-tier members receive member-specific predictions.
- [x] Test that exact reuse copies only allowlisted targets.
- [x] Prove crop, rotation, masks, profile, HDR, and excluded categorical or
  sparse families remain member-specific.
- [ ] Test independent fallback after representative failure, member failure,
  persistence failure, cancellation, and runtime pressure reduction.
- [x] Test batch bounds, duplicate IDs, unexpected operation items, image-byte
  limits, malformed payloads, and standard response envelopes.
- [ ] Test idempotent inference creation, application receipts, reconciliation,
  explicit outcomes, and interrupted-job recovery for reused members.
- [ ] Test the provenance migration, backup/restore, prune, reset, and inactive
  generation cleanup.
- [ ] Add synthetic recovery and labelled admission fixtures for true bursts,
  near-bursts, brackets, geometry changes, and cross-policy leakage.
- [x] Run full backend tests, Ruff checks, Lightroom plug-in validation, and
  translation synchronization.

Exit gate: all automated checks pass and reuse can be disabled to recover the
existing independent results.

## Phase 8: Evaluation and release gates

- [ ] Compare reuse output against the independent-inference oracle on a
  burst-preserving held-out dataset.
- [ ] Report per tier: eligible photos, admitted photos, selective coverage,
  policy agreement, target error by family, catastrophic error, confidence,
  ambiguity abstention, and cross-policy leakage.
- [ ] Report geometry disagreement even though geometry is excluded from reuse,
  confirming the exclusion remains necessary.
- [ ] Report cached/uncached wall time, embedding time, policy-inference time,
  peak image bytes, queue depth, and avoided policy predictions.
- [ ] Require exact-reuse precision and cross-policy leakage to meet approved
  thresholds before enabling the tier by default.
- [ ] Require no material regression in independent or policy-coherent target
  error, selective coverage, profile/HDR safety, application reliability, or
  explicit user outcomes.
- [ ] Exercise large operations under each hardware tier and pressure state;
  observed resource use must remain within detected or explicit maxima.
- [x] Stage rollout behind an internal versioned service gate, validate in a
  developer build, then enable conservative automatic behavior only after the
  evaluation gate passes.
- [x] Keep an immediate service-side kill switch that forces all photos to
  independent inference without changing stored preferences or schemas.
- [x] Update `docs/wiki/Architecture.md`, `Developer-Guide.md`, `Plugin-Guide.md`,
  `UI_BEHAVIOR_CONTRACTS.md`, and `UI_HUMAN_TEST_MATRIX.md` only when the
  corresponding production behavior is reachable.
- [ ] Mark this checklist as implemented, leaving only explicitly labelled
  human Lightroom/platform validation unchecked.

Exit gate: burst reuse is conservative, auditable, bounded, measurably useful,
and safe to enable without changing the meaning of Apply My Style.

## Human Lightroom validation

- [ ] HUMAN: Compare independent and optimized runs on real sports, wildlife,
  event, panning, bracketed, and mixed-camera sequences.
- [ ] HUMAN: Confirm global tone/color consistency improves without flattening
  legitimate frame-to-frame exposure differences.
- [ ] HUMAN: Confirm crop and rotation remain judicious and photo-specific.
- [ ] HUMAN: Confirm profile/HDR suggestions and Auto decisions remain
  photo-specific and preserve exact readback requirements.
- [ ] HUMAN: Confirm per-photo review, virtual-copy creation, output collection,
  selection, active source, progress, cancellation, Undo, and final summaries.
- [ ] HUMAN: Confirm behavior with cached embeddings, uncached RAW previews,
  missing previews, very large bursts, and runtime pressure changes.
- [ ] HUMAN: Validate macOS and Windows Lightroom behavior in every supported
  language before release.
