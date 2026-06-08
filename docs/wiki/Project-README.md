# Project README

> Auto-generated from `README.md`. Do not edit this page manually.

<div align="center">
  <h1>🌟 StyleAI</h1>
  <p><b>A smart Lightroom Classic plugin for AI-powered tagging, describing, semantic search, and develop edits.</b></p>
  
  [![Lua](https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white)]()
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)]()
  [![Website](https://img.shields.io/badge/Website-github.com/RareLight/StyleAI-00B2FF?style=for-the-badge)]()
  [![Downloads](https://img.shields.io/github/downloads/RareLight/StyleAI/total?style=for-the-badge&label=Downloads)](https://github.com/RareLight/StyleAI/releases)
</div>

---

## 📖 About the Project

**StyleAI** brings the power of modern Large Language Models (LLMs) directly into Adobe Lightroom Classic. It analyzes your photos, automatically generates accurate tags and detailed descriptions, creates AI-guided Lightroom develop edit recipes, and lets you rediscover your library with semantic free-text search using natural language. Whether you want to cull a massive photo shoot, find a specific photo using natural language, or have AI automatically suggest develop edits that match your style, StyleAI integrates directly into your existing workflow. 

**Your Privacy, First:** In an era where AI often means uploading your personal or client photos to the cloud, StyleAI flips the script. By leveraging highly optimized models (like SigLIP2, Ollama, and LM Studio), StyleAI runs powerful AI analysis **entirely locally on your own machine**. Your photos, your metadata, and your intellectual property never have to leave your hard drive unless you explicitly choose to connect a cloud provider.

---

## ✨ Core Features

- **🤖 AI-Powered Tagging & Describing:** Uses advanced LLMs to accurately recognize image content, generate metadata, and provide detailed descriptions of your photos.
- **🎛️ AI Lightroom Edit (Develop):** Generates a structured Lightroom edit recipe per photo (global adjustments and optional masks) and can apply it directly in Develop mode. Includes per-photo review, style presets, style strength, composition/crop mode, and per-photo instruction overrides.
- **🔍 Semantic Free-Text Search (Advanced Search):** Find images naturally through descriptive queries (e.g., *"Red sports car parked in front of a garage"* or *"Sunset over the mountains"*). StyleAI automatically creates a relevance-sorted Collection in Lightroom based on your prompt.
- **📸 Image Culling:** Group similar photos into bursts or near-duplicate stacks, automatically pick the strongest frames, and create Lightroom collections for picks, alternates, reject candidates, and optional duplicates.
- **100% Local Inference (Privacy First):** Protect your clients and your art. Run all embeddings, face recognition, and auto-tagging locally using Ollama or LM Studio. Your data stays on your machine.
- **Cloud Models Available:** If you need maximum reasoning capabilities, you can optionally connect to the cloud with ChatGPT, Google Gemini, and Vertex AI using your own API keys.
- **🎨 Customizable Prompts & Temperature Control:** System prompts for the AI can be added, edited, and deleted directly within the Lightroom Plug-In Manager. Use the temperature slider to control whether the AI should be highly creative or strictly consistent.
- **📝 Photo Context (Contextual Info):** Provide manual hints to the AI before analysis (e.g., names of people or specific background details) that aren't immediately obvious from the image itself. This can be done via a popup dialog or directly in Lightroom's metadata panel.
- **🗄️ Custom Python Backend & Database:** The plugin utilizes a high-performance local server (`styleai-server`). Existing metadata from your Lightroom catalog can easily be imported prior to the first AI analysis.

---

## 🚀 Installation & Getting Started

1. Download the latest release from the [GitHub Releases page](https://github.com/RareLight/StyleAI/releases).
2. Extract the ZIP file and add the plugin via the **Plug-in Manager** in Lightroom Classic.
3. **Backend Server Setup (First Launch):**
   - The backend starts automatically from Lightroom.
   - **Bypassing Security Warnings:** Because the installers are currently not code-signed, you will see warnings from **Windows SmartScreen** or **macOS Gatekeeper**.
     - **Windows:** Click *More info* -> *Run anyway*.
     - **macOS:** Right-click the `.pkg` -> *Open* -> *Open anyway*.
   - Optional troubleshooting: if you want to start it manually, run `styleai-server/styleai-server.cmd` on Windows or `styleai-server/styleai-server` on macOS.
4. Select photos in the library and choose one of the AI actions from **Library -> Plug-in Extras**:
   - **Analyze & Index Photos...** for tags/descriptions/search index
   - **AI Edit Photos...** to generate and apply Lightroom develop edits
   - **Advanced Search...** for semantic free-text search
5. For AI Edit, start with defaults, keep **Review each proposed edit before applying it** enabled, and tune style via **Overall look** + **Style strength**.

*For comprehensive details, model setup guides, and tips, please visit [github.com/RareLight/StyleAI/wiki](https://github.com/RareLight/StyleAI/wiki).*

---

For detailed instructions on how to use Google Vertex AI, please see our [Google Vertex AI Login Wiki Page](https://github.com/RareLight/StyleAI/wiki/Google-Vertex-AI-Login).

## ⚖️ License

The StyleAI core, plugin, and backend are released under the **GNU Affero General Public License v3 (AGPL-3.0)**. 

This project is built on the belief that AI tooling for creatives should remain open, transparent, and community-driven. See the [LICENSE](LICENSE) file for the full license text.


## 🛠️ Tech Stack

- **Frontend / Lightroom Plugin:** Lua (Lightroom SDK)
- **Backend / Server:** Python (`styleai-server`) / FastAPI / Flask
- **AI & Embedding:** Open-CLIP (SigLIP2), PyTorch, ONNX Runtime
- **Identity & Faces:** InsightFace
- **Database:** ChromaDB (Vector Search), SQLite (Metadata & Cache)
- **Supported Interfaces:** Google Gemini, Vertex AI, ChatGPT/OpenAI, Ollama, LM-Studio


---

## 🛠️ Development

For more detailed information on how to contribute, please see our [CONTRIBUTING.md](CONTRIBUTING.md).


## 🤝 Credits

Developed with a passion for photography and IT by:

- **Bastian Machek (LrGenius / Fokuspunk)** – *Creator & Lead Developer*
- **Community** – *Special thanks to all contributors and testers for your valuable input and support.*
- **Various AI agents** - *For the great support in developing this project.*

This project leverages many incredible open-source libraries, including **InsightFace**, **OpenCLIP**, **PyTorch**, **Hugging Face Transformers**, **ChromaDB**, and **Flask**. 

A huge thank you to the open-source community and the developers of the underlying AI frameworks that make this integration possible!
