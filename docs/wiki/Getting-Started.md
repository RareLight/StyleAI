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

## 2. Initial Setup & Onboarding

Once installed, we recommend using the new automated setup flow:
1. Select any photo in your Lightroom Library grid.
2. Navigate to `File -> Plug-in Extras -> Prepare Photos...`.
3. If this is your first time using StyleAI, the **Onboarding Wizard** will automatically launch. 

The Wizard will guide you step-by-step through:
- Connecting to your Python backend server.
- Seamlessly migrating any existing Lightroom metadata into the vector database.
- Selecting and configuring your preferred local AI provider.

### Manual Configuration
If you prefer to configure settings manually, open the **Lightroom Plug-in Manager** (`File -> Plug-in Manager`) and locate StyleAI. Here you will find cleanly organized tabs for:
- **Models & Prompts:** Configure your preferred local (Ollama/LM Studio) LLM providers.
- **Support & Diagnostics:** If you encounter issues, click **Generate Diagnostic Report** to automatically fetch server health and logs into a beautifully formatted HTML file.

*Having trouble? Refer to the [Troubleshooting](Troubleshooting) guide for connectivity and API issues.*

## 3. Optional: Semantic Search & Auto-Tagging

If you want to be able to search your photos using natural language (e.g., "red sports car in the rain") or automatically generate keywords and captions, you must "index" your photos. This uses a Large Language Model (LLM) to write text metadata.
1. Select one or more photos in your Lightroom Library grid.
2. Navigate to `File -> Plug-in Extras -> Prepare Photos...`.
3. The plugin will pass the photos to the backend, generate descriptions, tags, and AI embeddings, and store them in the Search database.

**Note:** You do **not** need to index photos if you only want to use the AI Editing or Style Training features! Those features run independently and skip the slow LLM keyword process entirely.

## 4. Create a DB Backup

StyleAI automatically keeps 14 daily validated backend snapshots and creates
required recovery points before destructive maintenance. To keep an additional
copy outside the catalog folder:
1. Open `File -> Plug-in Manager`.
2. Navigate to `Data & Recovery` and click **Export Backup...**.
3. Save the resulting `.zip` somewhere safe. It contains StyleAI indexes,
   training data, learned styles, and history.

Use **Restore Backup...** in the same section to restore a validated backup for
the active catalog. StyleAI backups do not contain your Lightroom catalog,
source photos, or Develop edits; continue using Lightroom's catalog backups.

## 5. Imported Help Pages

For further reading, we've migrated several curated guides from the project website:
- [Help: Prepare Photos](Help-Analyze-and-Index)
- [Help: Choosing AI Model](Help-Choosing-AI-Model)
- [Help: Ollama Setup](Help-Ollama-Setup)
- [Help: LM Studio Setup](Help-LM-Studio-Setup)
