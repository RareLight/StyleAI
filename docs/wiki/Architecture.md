# StyleAI Architecture & Data Pipelines

StyleAI is a local-first Adobe Lightroom Classic plugin. The Lua frontend owns
Lightroom UI and SDK operations; a loopback-only Python service owns image
analysis, local-model inference, vector retrieval, and catalog-local storage.
The active Lightroom catalog has exactly one adjacent `styleai.db`. The one
loopback service process is bound to that catalog/database pair and rejects
attempts to switch database paths. Because the database path is
`<catalog directory>/styleai.db`, catalogs must be kept in separate folders.
Backups carry a generated database marker and may restore only when that marker
matches; photo records use stable Lightroom/global photo IDs.

## Frontend and service boundary

Lightroom exposes six production workflows through **File > Plug-in Extras**:
Prepare Photos, Learn From My Edits, Apply My Style, Rate Selected AI Edits,
Styles & Training, and Find More Training Examples. Lua owns every Lightroom
SDK action. The backend never writes directly to the `.lrcat` file; it returns
work for Lightroom to commit through SDK catalog/Develop transactions.

Backend routes are thin HTTP boundaries. Services own durable operation jobs,
resource admission, Chroma/SQLite writes, ML fitting, inference, and evaluation.
Ollama and LM Studio adapters are used only by optional metadata generation.

## Core pipelines

### Photo analysis and indexing

1. Lightroom sends bounded rendered JPEG proxies, original-file paths, EXIF,
   keywords, and stable `globalPhotoId` values to the backend.
2. The backend keeps the rendered proxy for local-LLM metadata but uses the
   target-independent embedded RAW preview for SigLIP2 when available.
3. The hardware-tiered SigLIP2 worker stores a 1152-dimensional canonical
   source embedding plus source fingerprint, provenance, model, preprocessing,
   schema, and source metrics in the `image_embeddings` Chroma collection.
4. Metadata generation uses only locally running open-weights models through
   Ollama or LM Studio. Requests are batched and serialized so they do not
   contend with active embedding work for unified memory.
5. Each photo retains its own proxy bytes through its own vision inference.
   Similarity or burst grouping may schedule work, but metadata is never copied
   from a representative photo.

### Editing-policy training

1. `Learn From My Edits` stores unedited source evidence and the photo's absolute
   Lightroom Develop target in the isolated `edit_training` collection.
2. Panoramas are excluded. Photos within 10 seconds and SigLIP2 cosine distance
   0.05 are one burst; the deterministic hero receives weight `1 / burst_size`.
3. Training is partitioned only by incompatible HDR/profile state. Subject,
   genre, lighting, camera body, and lens do not create Cartesian style groups.
4. Lightroom preflights stable photo IDs before exporting previews, skips
   already-learned examples unless update was requested, and uploads bounded
   transport chunks without fitting between chunks. Compatible canonical
   source embeddings are reused and remaining RAW previews are embedded in
   pressure-aware batches. One explicit rebuild begins after the complete run.
5. Multi-output targets are robustly normalized during fitting so large-unit
   controls cannot dominate small-unit controls. Burst-grouped cross-validation
   selects a robust constant baseline,
   reduced-rank ridge, weighted PLS, or eligible multi-task Elastic Net
   independently for each compatible partition.
6. Grouped out-of-fold residuals from the selected broad model initialize
   distinct editing responses without allowing the much larger source-feature
   block to dominate policy identity. New photos are assigned through
   normalized cosine multi-medoid image-embedding gates.
7. Grouped cross-validation adds experts only when they produce a material
   held-out improvement with adequate support and acceptable ambiguity.
8. Shrunken hierarchical residual offsets calibrate supported
   HDR/camera/profile combinations without fragmenting policy identity.
   A policy-specific cosine-neighborhood corrector may then learn only grouped
   out-of-fold residuals. It is saved only when held-out error improves
   materially and coverage is adequate; sparse, distant, or high-variance
   neighborhoods abstain.
9. Repeated estimator and policy-count validation uses a deterministic,
   burst-preserving sample of at most 600 examples. Local correction validation
   and its residual bank use at most 2,048 examples. The selected final global
   model still fits all curated examples, so scaling does not impose a silent
   training-data ceiling.
10. The backend writes a complete inactive generation and versioned `joblib`
   artifacts, then atomically activates it. Failed or interrupted builds leave
   the prior active generation intact; successful activation prunes inactive
   derived generations and stale examples unless a nonterminal edit operation
   still pins that retired generation.
11. Camera-profile and HDR selectors train only from
   Lightroom-target-independent embedded RAW previews marked `raw_preview`.
   Lightroom-rendered previews and legacy indexed
   embeddings are rejected for categorical training because they can contain
   the target rendering treatment. Grouped out-of-fold nearest-centroid
   candidates must beat the compatible-camera majority baseline at high
   selective precision; otherwise they abstain.

### ML editing

1. The backend reuses the canonical source embedding and pixel metrics only
   when their complete source/model/schema stamp matches. A miss safely falls
   back to RAW-preview extraction and single-photo inference, then atomically
   refreshes the derived vector. Missing target-independent evidence forces
   categorical abstention.
2. A versioned selector proposes HDR first and then one catalog-observed,
   camera-compatible profile conditional on that HDR state. Off and Suggest
   preserve the applicable current state; Auto remains readback-gated. The
   Profile Auto is conditioned on the effective HDR state, never an unapplied
   HDR suggestion. The exact effective HDR/profile partition then selects a
   global policy artifact. Calibrated
   source membership either selects one policy or abstains. Competing policies
   are never blended, and catalog size never causes an abrupt model switch.
3. The selected policy predicts absolute Lightroom targets. When its local
   corrector passed training-time validation, up to 100 policy-local neighbors
   within cosine distance 0.15 may correct the global residual. The result is
   then clamped to Lightroom-safe and learned bounds; local abstention leaves
   the global prediction unchanged.
4. Application interpolates from the photo's current value to the target:
   `current + strength * (target - current)`. At full strength the result equals
   the target exactly, regardless of prior edits, and repeated application is
   idempotent.
5. Before returning the recipe, the backend persists an immutable inference
   containing the photo, generation/policy provenance, modeled slider set,
   pre-edit state fingerprint, and absolute target fingerprint. Lightroom
   returns the inference ID with an application outcome and modeled-slider
   readback; the backend appends this evidence without rewriting the inference.
6. Later reconciliation compares only the modeled sliders with the stored
   pre-edit and confirmed-application fingerprints. It records `reverted` or
   `diverged` as observable states without assuming that Lightroom Undo, a
   preset, or manual editing caused the change.
7. An explicit, user-invoked review action may append `accepted`, `rejected`,
   or `modified_and_kept` together with a fresh modeled-slider readback. Undo
   and divergence remain state observations and never become preference labels
   automatically.
8. Lightroom applies an Auto rendering state before any sliders, reads it back,
   and verifies the exact SDK representation. Failure prevents slider
   application and triggers one bounded restoration attempt. Lightroom 15.5
   reliably supports Undo for these SDK transactions but did not preserve Redo
   in the capability spike; rerunning an edit is not treated as Redo.

### Upgrade recommendations

1. All policies in a compatible partition retrieve bounded Chroma
   neighborhoods around their visual anchors in one Chroma query.
2. Existing training photos, panoramas, incompatible partitions, ambiguous
   memberships, rejected photos, and near-duplicates are excluded.
3. Candidate source features, membership, and empirical coverage are evaluated
   as matrix batches. Existing-example duplicate screening uses float32
   embeddings and bounded matrix blocks to use local BLAS throughput without an
   unbounded similarity matrix.
4. Burst representatives are selected deterministically.
5. Remaining candidates are ranked by policy membership, embedding-only
   empirical coverage gain, user rating/pick signals, and diversity.
6. Keywords and local visual tags may provide open-vocabulary explanations
   after admission. They never determine membership.
7. The backend stores one bounded, deterministic review snapshot per
   generation and policy. Lightroom may label selected recommendations as
   helpful, matching-but-redundant, or wrong-policy. Labels survive derived
   policy resets, retain generation/schema provenance, and never change the
   active model automatically. Embeddings remain canonical in Chroma and are
   joined only during an explicit local analysis export.

## Resource scaling

Indexing uses `config.get_index_resource_limits()`. Apple Silicon defaults for
GPU batch / admission queue / HTTP threads are:

- 16 GB unified memory: `8 / 32 / 8`
- 32 GB unified memory: `12 / 48 / 12`
- 64 GB or more: `16 / 64 / 16`

Policy discovery keeps repeated estimator/policy-count validation at 600
burst-safe examples and local residual validation at 2,048, while the selected
global model still fits every curated example. Recommendation membership and
coverage are matrix-batched; duplicate screening uses float32 embedding
artifacts and a maximum 16 MiB similarity workspace. Run
`uv run python scripts/benchmark_policy_scaling.py` from `server/` to measure
these paths at representative catalog sizes on the current machine.

For quality evaluation on real edits, run
`uv run python scripts/evaluate_catalog_policies.py --db-path
"/path/to/catalog/styleai.db"`. This performs burst-safe cross-validation with
the production fitting and inference path without persisting or activating the
fold artifacts. The aggregate report is catalog-local and versioned by a
deterministic dataset fingerprint.

The Lightroom Style Index and Upgrade Assistant report the model selected by
evidence (`Global conditional policy` or `Global + validated local refinement`);
they do not infer quality tiers from arbitrary training-count thresholds.

Applied-edit evaluation reads inference/event history in bounded, keyset-
paginated batches. It reports application reliability, explicit outcomes,
delivered-target corrections, confidence reliability, and per-generation
Wilson intervals. Its v2 contract also reports burst tier coverage, fallbacks,
policy/partition leakage, per-tier corrections, and geometry disagreement.
Generation comparisons are evidence-only and never activate models or modify
thresholds.

Apply My Style orders the operation by capture time and submits bounded groups
that normally follow the hardware batch recommendation. A temporal burst may
extend one request up to 64 photos to avoid an arbitrary boundary; accelerator
inference remains pressure-batched internally. Candidate construction compares
only temporal neighbors within the
established 10-second and 0.05 cosine-distance ceilings, splits transitive
components into bounded deterministic windows, and chooses a visual medoid.
Admission then requires compatible camera/profile evidence, rejects likely
brackets and panoramas, and verifies each member's independently selected
policy and effective rendering partition. Each recipe persists its own
operation, grouping/tier, source-delta, policy-agreement, absolute-target, and
fallback provenance.

`policy_coherent` members retain their own production prediction. The stricter
`global_target_reuse` path merges only a versioned scalar allowlist and then
applies member strength; white balance, sparse/structural targets, geometry,
profile/HDR remain member-specific. Learned mask application is not part of
Apply My Style. Exact reuse is release-gated off
by default. Critical runtime pressure reduces all members to independent
inference, and the service kill switch restores the independent path.

Local LLM concurrency remains one by default. Increasing concurrent model
requests usually reduces throughput through GPU context switching and unified
memory pressure.

## Durable work and lifecycle

Indexing, metadata, training/discovery, recommendations, and editing use
catalog-local operation jobs with per-photo state. Backend processing that
still needs a Lightroom metadata or Develop handoff remains nonterminal until
Lightroom reports the outcome. Cancellation is scoped to one job; maintenance
drains live inference-to-commit work through a writer-preferring barrier.

`WorkCoordinator.lua` bounds Lightroom export, backend request, catalog-write,
and Develop/UI lanes. The backend acquires complete resource vectors atomically,
so multiple simultaneous Lightroom tasks share rather than multiply accelerator
and local-LLM capacity.

Lightroom shutdown performs no service I/O and returns immediately. The backend
unloads idle SigLIP2 weights after 10 minutes and exits after 10 idle minutes
only when no operation, admission lease, or index queue work is live. Interrupted
jobs and derived discovery state are recovered conservatively on next startup.

## Storage

- `styleai.db/chroma.sqlite3`: isolated `image_embeddings` and `edit_training`
  Chroma collections.
- `styleai.db/styles.sqlite`: policy generations, versioned examples, model
  registrations, soft memberships, descriptors, coverage, validation results,
  custom policy names, immutable edit inferences (including operation-scoped
  burst provenance), and append-only edit events.
- `styleai.db/policy_v2_models/<generation>/`: immutable model artifacts for a
  generation.

External tools should treat these files as read-only. Visual-index and training
collections are intentionally isolated; clearing one must not silently erase the
other.
