# Troubleshooting

Start in **File → Plug-in Manager → StyleAI**. Status & Setup reports the local
service, SigLIP2 model, and optional metadata-provider readiness. Use **Help →
Plug-in Extras → StyleAI: Generate Support Report...** to collect a local
diagnostic report.

## Service is unreachable

- Confirm no unrelated application owns loopback port 19819.
- Use **Repair Background Service** when it appears.
- For source development, quit Lightroom and run
  `bash scripts/styleai-installer.sh redeploy`; this stops a recognized StyleAI
  backend, verifies port release, and atomically replaces the plug-in tree.
- If an unsigned packaged binary was quarantined, follow the exact release-note
  instructions for that downloaded package.

Do not manually kill an unknown process on port 19819. The management script
refuses to do so.

## Vision model is missing or failed

SigLIP2 is required for visual analysis, training, recommendations, and learned
editing. Use **Configure Local Models...** or, in a source checkout, run:

```sh
cd server
uv run python scripts/download_models.py
```

Check local cache permissions and free disk space. `/clip/status` and `/health`
contain the service-side readiness state.

## Metadata model is unavailable or slow

- Metadata generation requires a vision-capable Ollama or LM Studio model on
  the same computer. Learned editing does not.
- Ensure the provider is running and the model is loaded/listed.
- If macOS swaps or Lightroom becomes unresponsive, choose a smaller model and
  leave processing load on Automatic. Local LLM requests are already serialized;
  launching overlapping metadata batches queues rather than multiplying model
  contexts.
- Re-run only failed photos. Per-photo durable state allows the rest of a batch
  to complete.

## Selected-photo scope processes the wrong photos

Select photos in Library grid/filmstrip before opening the workflow. StyleAI
captures that target-photo selection before its modal dialog. Reload the
current plug-in if behavior differs, and include the plug-in log in a support
report.

## Metadata repeats another photo

Current metadata jobs retain each photo's own pixels and perform independent
vision inference. Do not continue a batch if output clearly describes a
different image. Generate a support report; if diagnostic capture is explicitly
enabled, inspect the affected local capture and clear it after troubleshooting.
Reprocess affected photos with **Replace selected StyleAI-generated data**.

## No learned styles or frequent abstention

- Run **Learn From My Edits...** or **Styles & Training → Rebuild Learned
  Styles** after changing training data.
- Each compatible HDR/profile partition needs at least 12 valid burst-curated
  examples. More examples may be required for stable multi-policy recognition,
  profile/HDR Auto eligibility, or underrepresented visual components.
- Panoramas and non-RAW/DNG files are excluded from training.
- Suggest can be available when Auto is not; Auto uses stricter selective
  precision, uncertainty, compatibility, and exact-readback gates.
- Do not weaken ambiguity gates to force coverage. Add representative edited
  examples and evaluate the policy instead.

## Apply My Style skips a photo

Expected safe skips include no active compatible partition, low/ambiguous
membership, missing target-independent evidence, unavailable profile/HDR state,
or Lightroom readback mismatch. The completion summary and logs distinguish
skips from errors. Apply My Style never falls back to an LLM edit.

## Undo and evaluation state

Lightroom Undo can revert applied settings, but the immutable inference remains
in StyleAI history. Reconciliation records `reverted` or `diverged`; neither is
treated as acceptance or rejection. Use **Rate Selected AI Edits...** only for
an explicit quality judgment. A StyleAI database restore cannot undo Lightroom
Develop changes.

## Discovery or recommendation rebuild fails

Styles & Training starts a background rebuild and polls durable status. A failed
candidate generation must leave the prior active generation usable. Generate a
support report before retrying; repeated attempts are coalesced rather than
fitting once per upload chunk.
