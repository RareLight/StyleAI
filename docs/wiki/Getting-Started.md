# Getting Started

Welcome to StyleAI! This guide will walk you through setting up the plugin, indexing your first batch of photos, and starting your AI-powered Lightroom workflow.

## 1. Install Plugin and Server

To begin, you must install both the Lightroom Classic plugin frontend and the Python backend server. These components communicate locally to process your images without freezing the Lightroom UI. 
Please refer to the high-level installation instructions on the [root `README.md`](Project-README) or the detailed steps in the [`plugin/README.md`](Plugin-README).

### Pre-Downloading AI Models
To prevent long delays or network timeouts the first time you run indexing, you should cache the AI models locally. From the `server` directory, run:
```bash
uv run python scripts/download_models.py
```

### ⚠️ Bypassing Security Warnings (Unsigned Installers)

Because StyleAI is an open-source project and the current installers are not code-signed, your operating system will likely flag them as "untrusted" or "malicious". This is a standard security precaution for any third-party software that has not been notarized by Microsoft or Apple.

#### Windows (SmartScreen)
When you run the installer or the backend `.cmd` file, you may see a "Windows protected your PC" dialog.
1. Click **More info**.
2. Click **Run anyway**.

#### macOS (Gatekeeper)
When you try to open the `.pkg` installer or the backend binary:
1. **Right-click** (or Control-click) the file in Finder.
2. Select **Open** from the menu.
3. In the dialog that appears, click **Open** again.
4. If it still fails, go to `System Settings -> Privacy & Security`, scroll down to the "Security" section, and click **Open Anyway**.

---

## 2. Configure Plugin

Once installed, open the **Lightroom Plug-in Manager** (`File -> Plug-in Manager`) and locate StyleAI. Here you need to:
- **Set the Backend Server URL:** This defaults to `http://127.0.0.1:19819` but if you're running the backend on a different machine (e.g. via Docker), update the address here.
- **Configure Provider/API Keys:** If you plan to use cloud providers like OpenAI or Google Gemini, enter your API keys. For local providers like Ollama or LM Studio, ensure their respective base URLs are correctly configured.

*Having trouble? Refer to the [Troubleshooting](Troubleshooting) guide for connectivity and API issues.*

## 3. Index Photos

Before AI editing, style learning, or metadata generation can work, the backend needs to process ("index") your photos.
1. Select one or more photos in your Lightroom Library grid.
2. Navigate to `Library -> Plug-in Extras -> Analyze & Index Photos`.
3. The plugin will pass the photos to the backend, generate descriptions, tags, and AI embeddings, and store them.

Once indexing finishes, try out **AI Edit Photos**, **Save as AI Training Examples**, or **AI Index Photos** workflows.

## 4. Create a DB Backup

We highly recommend creating regular backups of your backend data, especially before performing system updates or database maintenance:
1. Open `File -> Plug-in Manager`.
2. Navigate to `Backend Server` and click **Download DB backup**.
3. Save the resulting `.zip` file somewhere safe. The backup contains the full persistent backend directory including your embeddings and metadata databases.

## 5. Imported Help Pages

For further reading, we've migrated several curated guides from the project website:
- [Help: Analyze and Index](Help-Analyze-and-Index)
- [Help: Choosing AI Model](Help-Choosing-AI-Model)
- [Help: Ollama Setup](Help-Ollama-Setup)
- [Help: LM Studio Setup](Help-LM-Studio-Setup)
