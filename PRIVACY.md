# 🔒 Privacy Policy

**Last Updated:** April 11, 2026

> [!IMPORTANT]
> **StyleAI is local-first by design.**
> Your photos, metadata, and AI-generated data stay on your computer by default. We believe in "Privacy as a Feature," not an afterthought.

---

## 📖 Our Philosophy

StyleAI was built with a simple goal: to provide powerful AI tools for photographers without compromising their privacy. We understand that your photo library is personal, and your creative "DNA" (editing style) is your intellectual property. Images, metadata, embeddings, and local-model requests remain on the same machine as Lightroom; StyleAI does not support cloud model providers or remote backends.

---

## 💻 Local Processing

You have full control over where your data is processed. All analysis, tagging, and semantic indexing happen entirely on your machine using local models like **Ollama** or **LM Studio**. No image data or metadata is ever transmitted to external servers.

---

## 📊 Data Collection & Storage

| Data Type | Storage Location | Retention | Why we need it |
| :--- | :--- | :--- | :--- |
| **Photos & Previews** | 🏠 Local Drive | Persistent | To generate AI tags and edits. |
| **Photo Metadata** (EXIF/IPTC) | 🏠 Local SQLite | Persistent | To identify photos and camera profiles. |
| **Search Embeddings** | 🏠 Local ChromaDB | Persistent | To enable semantic "natural language" search. |
| **Face Templates** | 🏠 Local Database | Persistent | To group photos by recognized people. |
| **Style Profile** (DNA) | 🏠 Local Database | Persistent | To learn your editing preferences. |
| **Diagnostic Logs** | 🏠 Local / ☁️ Remote* | Per Issue | To troubleshoot plugin errors. |

*\*Remote logs are only uploaded when you manually initiate a "Diagnostic Report" or "Copy to Desktop" action for support.*

---

## 🛡️ Sensitive Data & Security

> [!NOTE]
> **No Hidden Analytics**: StyleAI does not include background tracking, telemetry, or "usage metrics" that monitor your clicks or workflow without your knowledge.

### 📸 Images and Face Recognition
We use **InsightFace** for local face clustering. These biometric templates are stored in your local backend database and are **never** shared with us or any third party.

---

## 📋 Local Inference Engines

StyleAI relies on these local tools:

*   [Ollama](https://ollama.com/) - *Fully Private*
*   [LM Studio](https://lmstudio.ai/) - *Fully Private*

---

## 📬 Contact & Control

As the developer, I (Bastian Machek) have no access to your data. If you have questions about how the plugin handles specific workflows, please reach out:

- **Website:** [github.com/RareLight/StyleAI/wiki](https://github.com/RareLight/StyleAI/wiki)
- **GitHub:** [Report an Issue](https://github.com/RareLight/StyleAI/issues)

> [!TIP]
> **100% Data Sovereignty:** Because StyleAI exclusively uses local inference engines and local vector databases, zero bytes of your library ever leave your local network.
