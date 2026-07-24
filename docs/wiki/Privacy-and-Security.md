# Privacy & Security in StyleAI

StyleAI is built from the ground up to protect your privacy and your clients' intellectual property. In an era where "AI" often means unconditionally uploading your entire photo library to remote servers, StyleAI takes a completely different approach.

We exclusively use **local AI models** to ensure your data is protected.

## How We Protect Your Data

### 1. 100% Local Processing
StyleAI is designed to function entirely offline.
* **Embeddings & Search:** The semantic search index (SigLIP2) runs entirely on your own CPU/GPU using an embedded ChromaDB database.
* **Face Detection:** The culling engine uses an optimized local InsightFace model to detect, cluster, and rank faces.
* **Local LLMs:** By connecting to local AI runners like **Ollama** or **LM Studio**, you can run massive reasoning models (like Llama 3 or Gemma) entirely on your own hardware. Your photos never leave your machine.

### 2. Complete EXIF Metadata Stripping
StyleAI exports a temporary JPEG to send to the backend. During this export process, we use strict Lightroom export settings to **strip all EXIF metadata**, including:
* GPS Coordinates and location data
* Camera serial numbers and lens info
* Original capture timestamps
* Any existing keywords or copyright fields

### 3. Image Downsizing
We never send your full-resolution raw files or high-quality JPEGs to the AI. Images are automatically constrained to 1024px on the longest edge. This provides enough resolution for the AI to understand the scene composition, but obscures extremely fine background text or microscopic details.


