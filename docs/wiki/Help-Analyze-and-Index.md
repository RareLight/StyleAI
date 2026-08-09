# Prepare Photos: Visual Analysis and Metadata

Open **File → Plug-in Extras → Prepare Photos...** after selecting the photos
you want to process.

## Choose work

- **Analyze photos for StyleAI** creates a SigLIP2 embedding used by learned
  policy matching and training recommendations. StyleAI prefers the camera's
  embedded RAW preview so the visual evidence does not contain the Lightroom
  treatment it is trying to learn.
- **Generate keywords and descriptions** runs the selected local Ollama or LM
  Studio vision-language model.
- Enabling both commits each photo's visual analysis before its metadata phase.

Choose selected photos, current view, all catalog photos, new/unprocessed
photos, or previously indexed photos. **Keep existing data** is the safe
default. **Replace selected StyleAI-generated data** reruns the enabled work;
it does not erase unrelated Lightroom metadata.

## Metadata Settings

This button appears when metadata generation is enabled.

**Metadata Output** controls keywords, title, caption, alt text, Lightroom
catalog writing, per-photo review, append/replace behavior, keyword hierarchy,
catalog keyword reuse, and bilingual synonyms.

**Model & Instructions** selects an available loopback model, output language,
low-bounded creativity, and a saved prompt template. Lower creativity usually
produces more repeatable factual output.

**Context** can provide GPS, existing Lightroom keywords, parent folder names,
or per-photo instructions. These values are sent only to the selected local
model. Per-photo instructions intentionally pause batch processing.

Every photo receives its own vision inference. StyleAI may queue similar or
burst photos efficiently, but it does not copy one photo's complete keywords,
title, caption, or alt text to another.

Preparing training photos separately is not required. Learn From My Edits
collects its own source evidence; Prepare Photos is most important for the
catalog-wide visual index and optional metadata.

After StyleAI changes its embedding model or source-evidence schema, previously
indexed photos are automatically considered eligible for analysis again. Run
**Prepare Photos** with visual analysis enabled to refresh them in bounded GPU
batches; existing Lightroom metadata is not erased by the safe default.
