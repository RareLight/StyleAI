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

Given the importance of your generated search indexes and AI metadata, the background service exposes a dedicated backup download flow:
- API endpoint: `GET /db/backup`
- Output: A comprehensive ZIP archive containing the complete DB directory (Chroma data, SQLite db, and associated JSON files).

**To create a backup via Lightroom:**
Open `File -> Plug-in Manager -> StyleAI -> Background Service` and click **Download DB backup**.

**When to backup:**
We highly recommend initiating a backup prior to running large one-time DB migrations, moving the service to a new machine, or purging the search index while attempting to preserve your ML training examples.
