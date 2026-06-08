<div align="center">
  <h1>StyleAI</h1>
  <p><b>Your privacy-first photography assistant inside Lightroom Classic.</b></p>
  
  [![Lua](https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white)]()
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)]()
  [![Website](https://img.shields.io/badge/Website-github.com/RareLight/StyleAI-00B2FF?style=for-the-badge)]()
  [![Downloads](https://img.shields.io/github/downloads/RareLight/StyleAI/total?style=for-the-badge&label=Downloads)](https://github.com/RareLight/StyleAI/releases)
</div>

---

## Welcome to StyleAI

StyleAI bridges the gap between modern Large Language Models (LLMs) and Adobe Lightroom Classic. It serves as a photography assistant that knows your library and helps you edit, tag, and organize your work efficiently.

Whether you want to cull a massive photo shoot, find a specific photo using natural language, or have AI automatically suggest develop edits that match your style, StyleAI integrates directly into your existing workflow. 

It's designed with your privacy in mind, allowing you to run powerful AI models entirely locally on your own machine, or connect to your preferred cloud providers.

---

## Core Features

- **Auto-Tagging & Describing:** Let the AI analyze your images to automatically generate accurate tags, metadata, and detailed captions, saving you hours of manual data entry.
- **AI-Guided Develop Edits:** StyleAI can review a photo and propose a tailored Lightroom develop recipe—including complex masks. You retain full control over the style strength and can provide custom manual instructions.
- **Semantic Free-Text Search:** Find photos using natural language. Just type exactly what you remember—"Red sports car parked in front of a snowy garage" or "Bride laughing in the rain"—and StyleAI will build a Lightroom Collection with the most relevant matches.
- **Smart Image Culling:** StyleAI groups similar bursts of photos, helps you pick out the sharpest frames, and effortlessly creates organized collections for your picks, alternates, and rejects.
- **Local & Cloud Models:** Work the way you want. Run models locally and securely using Ollama or LM Studio, or connect to the cloud with ChatGPT, Google Gemini, and Vertex AI. 
- **Creative Control:** Customize the AI's core system prompts right from the Lightroom Plug-In Manager. Adjust the temperature slider to control whether the AI provides literal descriptions or more creative ideas.

---

## Getting Started

1. **Download:** Get the latest release from our [GitHub Releases page](https://github.com/RareLight/StyleAI/releases).
2. **Install:** Unzip the file and add the plugin to Lightroom Classic via the **Plug-in Manager**.
3. **First Launch:**
   - The backend server starts automatically in the background. 
   - *Security Note:* Since our installers aren't code-signed yet, your OS may show a warning. 
     - **Windows:** Click *More info* -> *Run anyway*.
     - **macOS:** Right-click the `.pkg` -> *Open* -> *Open anyway*.
4. **Usage:** Select photos in your Library, go to **Library -> Plug-in Extras**, and choose:
   - **Analyze & Index Photos...** for auto-tagging and semantic search.
   - **AI Edit Photos...** for creative develop edits.
   - **Advanced Search...** to find photos using natural language.

*For model setup guides, and troubleshooting, visit our [Wiki](https://github.com/RareLight/StyleAI/wiki).*
*(For detailed instructions on using Google Vertex AI, see our [Vertex AI Login Guide](https://github.com/RareLight/StyleAI/wiki/Google-Vertex-AI-Login).)*

---

## License

We believe that AI tooling for creatives should remain open, transparent, and community-driven. 

The StyleAI core, plugin, and backend are released under the **GNU Affero General Public License v3 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for the full text.

## The Tech Stack

- **Lightroom Plugin:** Lua (Lightroom SDK)
- **Backend Server:** Python (`styleai-server`) / FastAPI / Flask
- **Vision & Embeddings:** Open-CLIP (SigLIP2), PyTorch, ONNX Runtime
- **Identity & Faces:** InsightFace
- **Database:** ChromaDB (Vector Search) & SQLite (Metadata & Caching)
- **AI Integrations:** Google Gemini, Vertex AI, ChatGPT/OpenAI, Ollama, LM-Studio

## Contributing & Credits

If you're a developer or a photographer with ideas, check out our [CONTRIBUTING.md](CONTRIBUTING.md) to see how you can help shape the future of StyleAI.

**Developed by:**
- **Bastian Machek (LrGenius / Fokuspunk)** – *Creator & Lead Developer*
- **The Open Source Community** – *Special thanks to all contributors, testers, and the developers of the underlying AI frameworks (InsightFace, OpenCLIP, PyTorch, ChromaDB).*
