<div align="center">
  <h1>StyleAI</h1>
  <p><b>A local-first photography assistant for Adobe Lightroom Classic.</b></p>

  [![Lua](https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white)]()
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)]()
  [![Downloads](https://img.shields.io/github/downloads/RareLight/StyleAI/total?style=for-the-badge&label=Downloads)](https://github.com/RareLight/StyleAI/releases)
</div>

## What StyleAI does

StyleAI learns how a photographer edits, applies those decisions to new RAW or
DNG photos, builds a local visual index, and can generate Lightroom metadata.
Photo analysis, learned editing, and catalog storage run on the user's machine.
Optional text generation uses an open-weights vision-language model running
locally through Ollama or LM Studio.

StyleAI does not support cloud AI providers or remote backend servers. The
service listens only on `127.0.0.1:19819`, and each Lightroom catalog owns one
adjacent `styleai.db`. Keep each catalog in its own folder so this path remains
one-to-one.

## Main workflows

Use **File → Plug-in Extras** in Lightroom Classic:

- **Prepare Photos...** creates SigLIP2 visual embeddings and optionally writes
  local-model keywords, titles, captions, and alt text.
- **Learn From My Edits...** learns absolute Develop targets from edited RAW and
  DNG examples. Training photos do not need to be prepared first.
- **Apply My Style...** selects a high-confidence learned policy, predicts
  absolute targets, and can conservatively suggest or apply HDR/profile choices.
  Ambiguous photos are left unchanged.
- **Rate Selected AI Edits...** records explicit, evaluation-only outcomes.
- **Styles & Training...** inspects, names, rebuilds, or deletes learned styles.
- **Find More Training Examples...** creates Lightroom collections of
  high-confidence photos that improve policy coverage.

Learned editing is mathematical and does not require Ollama or LM Studio.
Metadata generation does.

## Install and start

1. Download the correct package from [GitHub Releases](https://github.com/RareLight/StyleAI/releases).
2. Extract it and add `StyleAI.lrplugin` in Lightroom's **Plug-in Manager**.
3. If the unsigned backend is blocked, follow the operating-system instructions
   in the release notes.
4. Open StyleAI in Plug-in Manager and complete **Configure Local Models...**.

The plugin starts the packaged backend automatically. Source developers should
read the [Developer Guide](https://github.com/RareLight/StyleAI/wiki/Developer-Guide)
and [contribution guide](https://github.com/RareLight/StyleAI/blob/main/CONTRIBUTING.md).

## Architecture

- **Frontend:** Lua 5.1 and the Lightroom Classic SDK own UI, selection,
  metadata, collections, and Develop application.
- **Backend:** Python 3.12+, Flask/Waitress, and durable catalog-local jobs own
  image analysis, admission, training, prediction, and persistence.
- **Vision:** SigLIP2 through OpenCLIP/PyTorch.
- **Storage:** isolated Chroma visual/training collections plus transactional
  SQLite policy, recommendation, operation, and edit-history state.
- **Local metadata:** Ollama or LM Studio only.

See [Architecture](https://github.com/RareLight/StyleAI/wiki/Architecture),
[Privacy](https://github.com/RareLight/StyleAI/blob/main/PRIVACY.md), and the
[StyleAI Wiki](https://github.com/RareLight/StyleAI/wiki).

## License and credits

StyleAI is released under [AGPL-3.0](https://github.com/RareLight/StyleAI/blob/main/LICENSE).
It originated as a fork of
LrGeniusAI by Bastian Machek and has been extensively refactored and expanded by
Anna Grunseth and open-source contributors. See
[Credits](https://github.com/RareLight/StyleAI/wiki/Credits).
