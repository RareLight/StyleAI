# Lightroom Plug-in Guide

StyleAI's production entry point is **File → Plug-in Extras** in any Lightroom
module. The checked-in release exposes six commands.

## Prepare Photos

Creates local SigLIP2 visual embeddings, local-model metadata, or both. Scope
can be selected photos, current view, the catalog, new/unprocessed photos, or
previously indexed photos. Metadata settings control generated fields,
append/replace behavior, optional review, keyword organization, prompt,
language, and local-only context.

## Learn From My Edits

Reads—but does not change—the Develop settings of eligible RAW/DNG photos.
Training stores target-independent source evidence and absolute targets, then
builds one validated policy generation after the full upload. Panoramas and
unsupported formats are excluded. Preparing these photos first is unnecessary.

## Apply My Style

Uses the local vision model and learned policies; it does not use an LLM.
Controls include scope, 50/75/100% strength, profile and HDR Off/Suggest/Auto,
optional crop/straighten, virtual copies, per-photo review, and supported masks.
At 100%, modeled sliders reach absolute learned targets regardless of existing
edits. Crop and straighten remain independent permissions: when enabled,
StyleAI applies each only when the learned policy is sufficiently confident
that the corresponding action is appropriate for that photo. Low-confidence
or incompatible photos are skipped.

Apply My Style sends bounded, ordered batches to the local service. Within each
batch, StyleAI conservatively identifies likely burst members using capture
time, target-independent visual evidence, EXIF/source-exposure compatibility,
and the independently selected editing policy and rendering partition. A safe
member may be labelled **Coherent burst edit** in review, but it still keeps its
own source evidence, recipe history, crop/rotation decision, profile/HDR
decision, review choice, application receipt, and Develop readback. Unsafe or
ambiguous members use ordinary independent inference. There is no user setting
for this automatic optimization.

The stricter global-target reuse tier is implemented behind an internal service
release gate and is off by default until held-out and Lightroom validation
passes. When enabled for evaluation, it may share only an explicit continuous
tone/color/detail allowlist; it never shares white balance, crop, rotation,
profile, HDR, masks, or sparse/structural families.

## Rate Selected AI Edits

Records Keep, Modified and Kept, or Reject for up to 100 tracked selected
photos. This action reads current modeled sliders and appends evaluation
history; it neither changes Develop settings nor automatically retrains.

## Styles & Training

Shows active policies, rendering partitions, example counts, evidence cues,
and descriptions. It can show policy/all training photos, rename a policy,
start Find More Training Examples, rebuild the active generation, or delete all
training data after confirmation.

## Find More Training Examples

Retrieves high-confidence catalog candidates, filters incompatible/ambiguous
photos and burst duplicates, and creates collections under **StyleAI → Training
Recommendations**. Helpful, redundant, and wrong-policy labels are
evaluation-only.

## Plug-in Manager

- **Status & Setup:** service, SigLIP2, and optional metadata-model readiness.
- **Styles:** factual training/policy summary and access to Styles & Training.
- **Data & Recovery:** reveal the catalog-local database, export/restore a
  validated backup, and clean records for removed photos.
- **Support & Debug:** local support report, logs, hardware-load override, and
  explicitly gated diagnostic image capture.
- **Updates / About:** version, update, documentation, credit, and license links.

Each catalog must live in its own folder and owns the adjacent `styleai.db`.
Stable `globalPhotoId` values link Lightroom photos to local indexes, training,
recommendations, and history.
