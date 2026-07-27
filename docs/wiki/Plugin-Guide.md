# Plugin Guide

The StyleAI Lightroom Plug-in is the primary frontend for communicating with the AI backend. Through native Lua integrations and Lightroom dialogs, it provides a seamless user experience for extending your photography workflow with AI.

## Main Documentation

For detailed technical usage of the plugin component, please view the [`plugin/README.md`](Plugin-README).

## Core Workflows

The plugin handles the following core capabilities via the `Library -> Plug-in Extras` menu:

### 1. Analyze and Index
Passes the image files, metadata, and optional context directly to the backend to generate tags, structural descriptions, and embeddings for Semantic Search. This operation relies on LLMs to generate keywords and descriptions.

### 2. Train AI Style
Learns your personal editing style. Select your manually edited photos and run this task. The system will extract your Lightroom develop settings and calculate a pure-math visual embedding (SigLIP2) without needing an LLM or slowing down for keyword generation. You do **not** need to run "Analyze and Index" on these photos first.

### 3. AI Edit Photos
Predicts and applies absolute edits from your trained editing policies. This is
a fast, LLM-free local process: an embedding-only source gate selects a
high-confidence policy, and a burst-validated conditional regressor predicts
the Lightroom targets. Ambiguous photos are left unchanged for safety.

### 4. Advanced Search
Invokes semantic search. Unlike keyword search, semantic search translates natural language queries (e.g. "red sports car in a dark alley") into vectors that are compared against the visual embeddings of your images. Matches are grouped into a new Lightroom Collection, sorted by relevance.

### 5. Image Culling
Instead of manually comparing bursts of similar photos, the culling workflow analyzes time-grouped shots for sharpness, eye contact, and expression, grading them from best to worst. The plugin will create a structured Collection Set to categorize Picks, Alternates, and Rejects automatically.

### 6. Metadata Import and Retrieval
- **Import:** Syncs your existing Lightroom catalog metadata into the backend, improving subsequent AI tagging logic by giving the LLM existing context.
- **Retrieval:** If you generate AI tags on the backend but opt not to write them into Lightroom immediately, you can fetch them back later using the Retrieval utility.

### 7. Error Management
Errors no longer fail silently into log files. If a batch indexing task encounters issues (like a network timeout or an API authentication failure), the plugin provides a **Task Completion Dialog**. This aggregates the successes and details exactly what went wrong for any omitted files, making troubleshooting immediate and straightforward. For more info, see the [Troubleshooting](Troubleshooting) guide.

## Catalog-local identity

Stable `globalPhotoId` values are scoped to the active Lightroom catalog.
StyleAI does not migrate, claim, or route records between catalogs; each
catalog owns its adjacent `styleai.db`.
