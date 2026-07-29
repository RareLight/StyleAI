# StyleAI Architecture & Data Pipelines

StyleAI is a local-first Adobe Lightroom Classic plugin. The Lua frontend owns
Lightroom UI and SDK operations; a loopback-only Python service owns image
analysis, local-model inference, vector retrieval, and catalog-local storage.
Each Lightroom catalog has exactly one adjacent `styleai.db` and one backend
process bound to that database.

## Core pipelines

### Photo analysis and indexing

1. Lightroom exports bounded JPEG proxies and sends EXIF, keywords, and stable
   `globalPhotoId` values to the backend.
2. The backend uses bounded CPU preprocessing and hardware-tiered SigLIP2
   batches to create 1152-dimensional image embeddings.
3. Search embeddings are stored in the `image_embeddings` Chroma collection.
4. Metadata generation uses only locally running open-weights models through
   Ollama or LM Studio. Requests are batched and serialized so they do not
   contend with active embedding work for unified memory.

### Editing-policy training

1. `Train AI Style` stores unedited source evidence and the photo's absolute
   Lightroom Develop target in the isolated `edit_training` collection.
2. Panoramas are excluded. Photos within 10 seconds and SigLIP2 cosine distance
   0.05 are one burst; the deterministic hero receives weight `1 / burst_size`.
3. Training is partitioned only by incompatible HDR/profile state. Subject,
   genre, lighting, camera body, and lens do not create Cartesian style groups.
4. Lightroom uploads bounded transport chunks without fitting between chunks;
   one explicit rebuild begins after the complete training run is saved.
5. Burst-grouped cross-validation selects a robust constant baseline,
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
   derived generations and stale examples.

### ML editing

1. The backend computes the new photo's source embedding and pixel metrics.
2. The exact HDR/profile partition selects a global policy artifact. Calibrated
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

The Lightroom Style Index and Upgrade Assistant report the model selected by
evidence (`Global conditional policy` or `Global + validated local refinement`);
they do not infer quality tiers from arbitrary training-count thresholds.

Local LLM concurrency remains one by default. Increasing concurrent model
requests usually reduces throughput through GPU context switching and unified
memory pressure.

## Storage

- `styleai.db/chroma.sqlite3`: Chroma collections, including
  `image_embeddings`, `edit_training`, and face vectors.
- `styleai.db/styles.sqlite`: policy generations, versioned examples, model
  registrations, soft memberships, descriptors, coverage, validation results,
  and custom policy names.
- `styleai.db/policy_v2_models/<generation>/`: immutable model artifacts for a
  generation.

External tools should treat these files as read-only. Search and training
collections are intentionally isolated; clearing one must not silently erase
the other.
