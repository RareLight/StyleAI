<div align="center">
  <h1>🌟 StyleAI</h1>
  <p><b>Your intelligent, privacy-first photography assistant right inside Lightroom Classic.</b></p>
  
  [![Lua](https://img.shields.io/badge/Lua-2C2D72?style=for-the-badge&logo=lua&logoColor=white)]()
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)]()
  [![Website](https://img.shields.io/badge/Website-github.com/RareLight/StyleAI-00B2FF?style=for-the-badge)]()
  [![Downloads](https://img.shields.io/github/downloads/RareLight/StyleAI/total?style=for-the-badge&label=Downloads)](https://github.com/RareLight/StyleAI/releases)
</div>

---

## 📖 Welcome to StyleAI!

Imagine having a brilliant photography assistant who never sleeps, knows your entire library inside out, and helps you edit, tag, and organize your work at lightning speed. That's **StyleAI**.

StyleAI seamlessly bridges the gap between modern Large Language Models (LLMs) and Adobe Lightroom Classic. Whether you want to quickly cull a massive photo shoot, find that *one* specific sunset photo you took years ago using natural language, or have AI automatically suggest develop edits that match your unique style—StyleAI has your back. 

Best of all? It's designed with your privacy in mind, allowing you to run powerful AI models entirely locally on your own machine, or connect to your favorite cloud providers if you prefer!

---

## ✨ What Can StyleAI Do?

- **🤖 Effortless Auto-Tagging & Describing:** Tired of manually typing keywords? Let the AI analyze your images to automatically generate incredibly accurate tags, rich metadata, and detailed captions in seconds.
- **🎛️ AI-Guided Develop Edits:** It’s like having a digital retoucher by your side. StyleAI can review a photo and propose a tailored Lightroom develop recipe—including complex masks! You retain full control over the style strength and can even give the AI custom manual instructions.
- **🔍 Semantic Free-Text Search:** Say goodbye to digging through endless folders. Just type exactly what you remember—*"Red sports car parked in front of a snowy garage"* or *"Bride laughing in the rain"*—and StyleAI will instantly build a Lightroom Collection with the most relevant matches.
- **📸 Smart Image Culling:** Let the AI do the heavy lifting after a long shoot. StyleAI groups similar bursts of photos, helps you pick out the sharpest and strongest frames, and effortlessly creates organized collections for your picks, alternates, and rejects.
- **☁️ Your Choice of AI Brain:** Work the way you want! Run models locally and securely using **Ollama** or **LM Studio**, or tap into the cloud with **ChatGPT**, **Google Gemini**, and **Vertex AI**. 
- **🎨 Complete Creative Control:** You are the boss. Customize the AI's core system prompts right from the Lightroom Plug-In Manager. Tweak the "temperature" slider to decide whether the AI should give you safe, literal descriptions or wildly creative ideas.

---

## 🚀 Getting Started

Ready to supercharge your Lightroom workflow? Here's how to get up and running:

1. **Download:** Grab the latest release from our [GitHub Releases page](https://github.com/RareLight/StyleAI/releases).
2. **Install:** Unzip the file and add the plugin to Lightroom Classic via the **Plug-in Manager**.
3. **First Launch Magic:**
   - The backend server will quietly start itself automatically in the background. 
   - *A quick note on security:* Since our installers aren't code-signed yet, your OS might raise an eyebrow. 
     - **Windows:** Click *More info* -> *Run anyway*.
     - **macOS:** Right-click the `.pkg` -> *Open* -> *Open anyway*.
4. **Take It for a Spin:** Select some photos in your Library, go to **Library -> Plug-in Extras**, and choose:
   - **Analyze & Index Photos...** to unlock auto-tagging and semantic search.
   - **AI Edit Photos...** to generate and apply creative develop edits.
   - **Advanced Search...** to find photos using natural language.

*Looking for deeper dives, model setup guides, or troubleshooting? Our [Wiki](https://github.com/RareLight/StyleAI/wiki) is packed with helpful resources!*
*(For detailed instructions on using Google Vertex AI, see our [Vertex AI Login Guide](https://github.com/RareLight/StyleAI/wiki/Google-Vertex-AI-Login).)*

---

## ⚖️ License

We strongly believe that AI tooling for creatives should remain open, transparent, and community-driven. 

The StyleAI core, plugin, and backend are released under the **GNU Affero General Public License v3 (AGPL-3.0)**. Check out the [LICENSE](LICENSE) file for the full legal text.

## 🛠️ The Tech Behind the Magic

- **Lightroom Plugin:** Lua (Lightroom SDK)
- **Backend Server:** Python (`styleai-server`) / FastAPI / Flask
- **Vision & Embeddings:** Open-CLIP (SigLIP2), PyTorch, ONNX Runtime
- **Identity & Faces:** InsightFace
- **Database:** ChromaDB (Vector Search) & SQLite (Metadata & Caching)
- **AI Integrations:** Google Gemini, Vertex AI, ChatGPT/OpenAI, Ollama, LM-Studio

## 🤝 Contributing & Credits

We love contributions! If you're a developer or a photographer with great ideas, check out our [CONTRIBUTING.md](CONTRIBUTING.md) to see how you can help shape the future of StyleAI.

**Brought to life by:**
- **Bastian Machek (LrGenius / Fokuspunk)** – *Creator & Lead Developer*
- **The Incredible Open Source Community** – *Special thanks to all of our contributors, testers, and the developers of the underlying AI frameworks (InsightFace, OpenCLIP, PyTorch, ChromaDB) that make this dream possible!*
