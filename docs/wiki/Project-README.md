# Project README

> Auto-generated from `README.md`. Do not edit this page manually.

<div align="center">
  <h1>StyleAI</h1>
  <p><b>Your privacy-first, local AI photography assistant for Adobe Lightroom Classic.</b></p>
  
  [![Lua](https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white)]()
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)]()
  [![Website](https://img.shields.io/badge/Website-github.com/RareLight/StyleAI-00B2FF?style=for-the-badge)]()
  [![Downloads](https://img.shields.io/github/downloads/RareLight/StyleAI/total?style=for-the-badge&label=Downloads)](https://github.com/RareLight/StyleAI/releases)
</div>

---

## Welcome to StyleAI

StyleAI bridges the gap between modern Machine Learning/AI and Adobe Lightroom Classic. It serves as an intelligent photography assistant that understands your catalog and helps you edit, tag, search, and organize your work efficiently.

Whether you want to cull a massive photo shoot, find a specific photo using natural language, or have AI automatically apply develop edits that match your signature style, StyleAI integrates directly into your existing workflow. 

**Your Privacy, First:** In an era where AI often means uploading your personal or client photos to the cloud, StyleAI flips the script. By leveraging highly optimized vision models (SigLIP2) and embeddings (InsightFace), StyleAI runs its core indexing and predictive editing **entirely locally on your own machine**. Your photos, your metadata, and your intellectual property never have to leave your hard drive. 

> [!IMPORTANT]
> **Privacy First**
> StyleAI is designed as a local, privacy-first tool. Our Predictive ML Editing, Face Recognition, Semantic Search, and Auto-Tagging operate 100% locally. 
> 
> For a full breakdown of how we protect your data (including EXIF stripping and downsizing), please read our full **[Privacy and Security Explainer](https://github.com/RareLight/StyleAI/wiki/Privacy-and-Security)**.

---

## Core Features

- **Predictive ML Editing (Fast & Local):** StyleAI learns your "Signature Style" by analyzing your past edits. It creates specialized profiles and uses local machine learning to predict and apply develop recipes (including complex masks) to new photos that match similar lighting and scene conditions.
- **Generative AI Editing (Creative Fallback):** Need something totally out of the box? Fallback to generative AI to prompt completely custom edits using natural language.
- **Auto-Tagging & Describing:** Let the AI analyze your images to automatically generate accurate tags, titles, and hierarchical keywords, saving you hours of manual data entry.
- **Semantic Free-Text Search:** Find photos using natural language. Just type exactly what you remember—"Red sports car parked in front of a snowy garage" or "Bride laughing in the rain"—and StyleAI will build a Lightroom Collection with the most relevant matches.
- **Smart Image Culling & Face Recognition:** StyleAI groups similar bursts of photos, clusters faces locally (using InsightFace), helps you pick out the sharpest frames, and effortlessly creates organized collections for your picks and rejects.
- **100% Local Inference (Privacy First):** Protect your clients and your art. Run all core embeddings, face recognition, and predictive styling locally.
- **Local LLMs Available:** If you need maximum zero-shot reasoning capabilities, you can optionally connect to local LLMs via Ollama/LM-Studio.

---

## Getting Started

1. **Download:** Get the latest release from our [GitHub Releases page](https://github.com/RareLight/StyleAI/releases).
2. **Install:** Unzip the file and add the plugin to Lightroom Classic via the **Plug-in Manager**.
3. **First Launch:**
   - The StyleAI Background Service starts automatically. 
   - *Security Note:* Since our installers aren't code-signed yet, your OS may show a warning. 
     - **Windows:** Click *More info* -> *Run anyway*.
     - **macOS:** Right-click the `.pkg` -> *Open* -> *Open anyway*.
4. **Usage:** Select photos in Lightroom, go to **File -> Plug-in Extras**, and choose:
   - **Prepare Photos...** for local visual analysis, semantic search, and optional metadata generation.
   - **Learn From My Edits...** to teach StyleAI your editing decisions from edited RAW and DNG photos.
   - **Apply My Style...** to apply learned edits with visible review and virtual-copy safeguards.
   - **AI Edit Photos...** to apply your predictive learned styles or creative generative edits.
   - **Advanced Search...** to find photos using natural language.

*For model setup guides and troubleshooting, visit our [Wiki](https://github.com/RareLight/StyleAI/wiki).*

---

## The Tech Stack

StyleAI uses a split frontend/backend architecture to bypass Lightroom's Lua limitations.

- **Lightroom Plugin:** Lua 5.1 (Lightroom SDK) handles the UI, catalog interaction, and metadata application.
- **Background Service:** Python/Flask backend (`uv` managed) handles all heavy lifting asynchronously.
- **Vision & Embeddings:** Open-CLIP (SigLIP2), PyTorch.
- **Identity & Faces:** InsightFace.
- **Database:** ChromaDB (Vector Search with isolated collections for search vs. training) & SQLite (Metadata).
- **AI Integrations:** Ollama, LM-Studio.

## License

We believe that AI tooling for creatives should remain open, transparent, and community-driven. 

The StyleAI core, plugin, and backend are released under the **GNU Affero General Public License v3 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for the full text.

## Contributing & Credits

If you're a developer or a photographer with ideas, check out our [CONTRIBUTING.md](CONTRIBUTING.md) to see how you can help shape the future of StyleAI.

**Original Upstream Development by:**
- **Bastian Machek (LrGenius / Fokuspunk)** – *Creator & Lead Developer*
- **Refactor and expanded ML/AI capabilities by Anna Grunseth (Rare Light Photography)**
- **The Open Source Community** – *Special thanks to all contributors, testers, and the developers of the underlying AI frameworks (InsightFace, OpenCLIP, PyTorch, ChromaDB).*
