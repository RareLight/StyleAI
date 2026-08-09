# Getting Started

## Install

1. Download the correct macOS or Windows archive from the StyleAI releases page.
2. Extract the complete archive; keep the plug-in and packaged backend in their
   distributed layout.
3. Add `StyleAI.lrplugin` in **File → Plug-in Manager**.
4. If the unsigned backend is blocked, follow the platform-specific release
   notes to authorize that exact downloaded binary.

The plug-in starts a loopback service automatically and binds it to
`styleai.db` beside the active Lightroom catalog. Do not point two catalogs at
the same StyleAI database. Keep each Lightroom catalog in its own folder,
because the database path is fixed to `<catalog folder>/styleai.db`.

Source developers should instead run `bash scripts/setup-local-uv-env.sh`, add
`plugin/StyleAI.lrdevplugin`, and follow the [Developer Guide](Developer-Guide).

## Configure local models

Open StyleAI in Plug-in Manager and choose **Configure Local Models...**. The
setup view checks:

- whether the background service is reachable;
- whether the SigLIP2 vision model is cached and ready; and
- which local Ollama or LM Studio vision-language models are available.

SigLIP2 is required for visual analysis, training, recommendations, and learned
editing. Ollama or LM Studio is required only for generated keywords, titles,
captions, or alt text.

Developers can pre-cache SigLIP2 from `server/`:

```sh
uv run python scripts/download_models.py
```

## Prepare photos

Select photos, then open **File → Plug-in Extras → Prepare Photos...**.

- **Analyze photos for StyleAI** creates the visual index used by matching and
  recommendations.
- **Generate keywords and descriptions** uses the selected local metadata model.
- Select either task or both. In a combined run, each embedding commits before
  that photo's metadata phase.

Training photos do not have to be prepared first; Learn From My Edits exports
the source evidence it needs. Apply My Style can also analyze an eligible photo
at inference time, but preparing the broader catalog is important for finding
additional training examples.

## Learn and apply a style

1. Select representative, manually edited RAW/DNG photos.
2. Run **Learn From My Edits...**. A compatible profile/HDR partition needs at
   least 12 valid burst-curated examples before it can produce a policy.
3. Inspect learned policies in **Styles & Training...**.
4. Run **Apply My Style...**. Keep virtual copies and per-photo review enabled
   while validating a new policy.
5. Use **Rate Selected AI Edits...** to record explicit outcomes. Feedback is
   evaluation evidence and does not silently retrain or change thresholds.
6. Use **Find More Training Examples...** to review high-confidence candidates
   that broaden an existing policy's coverage.

Ambiguous photos are skipped rather than edited with a weak or unrelated match.

## Back up StyleAI data

StyleAI automatically keeps up to 14 daily validated snapshots and creates
required recovery points before destructive maintenance. In Plug-in Manager,
use **Data & Recovery → Export Backup...** for an external ZIP and **Restore
Backup...** for a validated same-catalog restore.

These archives contain the StyleAI visual index, training examples, policies,
operation history, and evaluation evidence. They do not contain the Lightroom
catalog, original photos, or Develop history.
