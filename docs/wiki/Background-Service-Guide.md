# Background Service Guide

The Python backend acts as the brains of StyleAI. It runs locally via Flask and handles predictive Machine Learning (ML) inference, image embedding generation using SigLIP2, face recognition using InsightFace, and vector database management.

## Main Documentation

For configuration settings, dependency management, and architecture details, refer to the [Background Service README](Background-Service-README).

## Key Responsibilities

The background service is responsible for:
- **Image Indexing:** Offloading heavy ML workloads (like SigLIP2 and InsightFace processing) away from the Lightroom UI using asynchronous background threads.
- **Predictive AI Editing:** Using validated mixtures of conditional regressors selected per partition by burst-grouped validation to infer absolute Lightroom targets from source evidence, with burst curation, hierarchical camera calibration, and ambiguity-aware abstention.
- **Semantic Search:** Executing fast, vector-based similarity searches using ChromaDB.
- **Metadata Persistence:** Keeping a high-performance secondary SQLite database for tags, face matching, style training, and other AI-generated text.
- **Face & Person APIs:** Processing and matching facial data to build identity maps over time.
- **Local Generative Features:** Providing batched metadata and optional creative operations through local open-weights models in Ollama or LM Studio only.

## Error Handling & Logic

The API is structured to return robust Error responses formatted in a standardized JSON envelope (`results`, `error`, `warning`). In the event of batch processing failures, endpoints will format exact stack traces detailing which images failed and why. This structured data is intercepted by the Lightroom plugin to generate user-friendly GUI error reports. 

If you are experiencing unexpected backend behavior:
1. Try parsing the terminal output or log files written to the service's working directory. 
2. Refer to the [Troubleshooting](Troubleshooting) wiki page to debug the service connection.

## Database Backup Workflow

Given the importance of generated search indexes, training data, learned styles,
and edit history, the background service creates validated catalog-local
snapshots. Each ZIP contains the complete StyleAI database directory plus a
versioned manifest, catalog ownership ID, file sizes, and SHA-256 checksums.
SQLite databases are copied through SQLite's online backup API and checked
before the archive is published.

- Manual export API: `POST /db/backup`
- Restore API: `POST /db/restore`
- Automatic retention: one daily snapshot, keeping the newest 14
- Required snapshots: before pruning, deleting all training data, schema
  migration, and restore

**To create a backup via Lightroom:**
Open `File -> Plug-in Manager -> StyleAI -> Background Service` and click **Download DB backup**.

Use **Restore Backup...** to select a validated backup belonging to the active
Lightroom catalog. StyleAI creates a pre-restore recovery snapshot, stages and
validates the selected archive, atomically replaces the backend database, and
rolls back automatically if post-restore validation fails.

> StyleAI backups do not contain the Lightroom catalog, source photos, or
> Develop edits. Use Lightroom's catalog backup feature separately.

**When to backup:**
Create a manual external backup before moving a catalog to another machine or
performing unusual maintenance. Routine indexing and Apply My Style operations
do not create full checkpoints because they use durable per-photo operation
state and a StyleAI database restore cannot undo Lightroom Develop edits.
