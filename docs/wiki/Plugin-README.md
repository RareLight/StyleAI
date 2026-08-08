# Plugin README

> Auto-generated from `plugin/README.md`. Do not edit this page manually.

# StyleAI Lightroom Plugin

AI-powered metadata, semantic search, and face workflows for Adobe Lightroom Classic.

---

## What It Does

StyleAI adds a backend-powered AI layer to Lightroom Classic. It helps you:

- Generate metadata (`title`, `caption`, `keywords`, `alt_text`)
- Run semantic search on your catalog
- Detect, cluster, and browse people/faces
- Run image culling on selections or the current view and create result collections for fast review
- Re-import generated metadata back into Lightroom

The plugin uses locally running open-weights models through Ollama or LM Studio while keeping Lightroom as your main workspace.

---

## Core Features

### Prepare Photos

- Batch-process selected, visible, all, or missing photos
- Generate embeddings for semantic retrieval
- Generate metadata
- Optional face detection and clustering

### Advanced Search

- Semantic search using image/text embeddings
- Metadata field search (`keywords`, `caption`, `title`, `alt_text`)
- Scope search to current selection/view/catalog

### People Workflows

- Cluster faces into persons
- Rename persons
- Jump from a person directly to a Lightroom collection

### Metadata Sync

- Import existing Lightroom metadata to backend
- Retrieve generated metadata from backend
- Apply validated values back to catalog

---

### Image Culling

- Cull similar photos from **selected photos** or the **current view**
- Group near-duplicates and bursts using backend similarity signals
- Rank photos into:
  - `Picks`
  - `Alternates`
  - `Reject Candidates`
  - optional `Duplicates / Near Duplicates`
- Create a dedicated Lightroom collection set for each culling run and switch you directly to the picks collection for review

---

## Requirements

- Adobe Lightroom Classic (supported by plugin SDK settings)
- StyleAI backend server reachable from Lightroom

---

## Installation

1. Build or download the plugin package.
2. In Lightroom Classic, open `File -> Plug-in Manager`.
3. Click `Add` and select the `StyleAI.lrdevplugin` folder.
4. Configure local Ollama or LM Studio provider settings in plugin preferences.

---

## Breaking Change: ID Migration Required

The plugin/backend now use file-based `photo_id` values instead of Lightroom catalog UUIDs as primary IDs.
The stable ID algorithm was updated again to avoid ID changes when metadata is written into files (for example DNG metadata updates).

If you already have an indexed backend database from older versions, run this one-time migration:

1. Open `File -> Plug-in Manager`
2. Select `StyleAI`
3. In the `Backend Server` section, click **Migrate existing DB IDs to photo_id**
4. Wait for the `LrProgressScope` migration to finish

Notes:

- Migration is incremental and skips photos that are not indexed in backend.
- Existing migrated entries are skipped automatically.
- Main embeddings and face references are migrated.

---

## Catalog-local storage

Each Lightroom catalog owns its adjacent `styleai.db` directory. StyleAI does not support shared databases, remote backends, or cross-catalog record routing. Stable Lightroom UUIDs and photo IDs are retained to repair local catalog mappings.

---

## Configuration (Plugin Manager)

In the plugin settings dialog you can configure:

- Local Ollama and LM Studio model selection
- Export size and quality used for AI processing
- Prompt presets
- Optional CLIP model download for advanced search

---

## Typical Workflow

1. Run **Prepare Photos**
2. Optionally validate generated metadata
3. Use **Advanced Search** to find related images
4. Use **People** and **Find Similar Faces** for portrait-heavy catalogs
5. Run **Cull Similar Photos** on a selection or the current view to create Picks / Alternates / Reject Candidates collections
6. Re-run **Import Metadata from Catalog** if needed for sync

---

## Migration Notes

If you migrated from legacy UUID-based IDs to `photo_id`:

- The plugin can trigger backend migration from the Plugin Manager UI.
- Migration uses a progress scope and batch requests.
- Existing collections (main embeddings, faces) are migrated through backend migration endpoints.

---

## Troubleshooting

- Verify the local StyleAI background service is running on loopback port `19819`.
- Check log files from Plugin Manager (`Show logfile` / copy logs to desktop).
- If search returns no results, confirm photos were indexed with embeddings.
- If faces are missing, ensure face processing was enabled during indexing.

---

---

## ⚖️ License

The StyleAI plugin is released under the **GNU Affero General Public License v3 (AGPL-3.0)**. 

---

## Documentation

- **Website/Help:** [https://github.com/RareLight/StyleAI/wiki](https://github.com/RareLight/StyleAI/wiki) (updated for v2.13.0)
- **GitHub Wiki:** [https://github.com/RareLight/StyleAI/wiki](https://github.com/RareLight/StyleAI/wiki)
- **Repository:** [https://github.com/RareLight/StyleAI](https://github.com/RareLight/StyleAI)
