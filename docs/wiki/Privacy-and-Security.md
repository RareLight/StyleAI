# Privacy & Security in StyleAI

StyleAI is built from the ground up to protect your privacy and your clients' intellectual property. In an era where "AI" often means unconditionally uploading your entire photo library to remote servers, StyleAI takes a completely different approach.

We strongly recommend using **local AI models** whenever possible, but if you do choose to use cloud APIs, we have implemented several safeguards to ensure your data is protected.

## How We Protect Your Data

### 1. 100% Local Processing Supported
StyleAI is designed to function entirely offline.
* **Embeddings & Search:** The semantic search index (SigLIP2) runs entirely on your own CPU/GPU using an embedded ChromaDB database.
* **Face Detection:** The culling engine uses an optimized local InsightFace model to detect, cluster, and rank faces.
* **Local LLMs:** By connecting to local AI runners like **Ollama** or **LM Studio**, you can run massive reasoning models (like Llama 3 or Gemma) entirely on your own hardware. Your photos never leave your machine.

### 2. Complete EXIF Metadata Stripping
If you choose to use a cloud model (or even a local model), StyleAI exports a temporary JPEG to send to the backend. During this export process, we use strict Lightroom export settings to **strip all EXIF metadata**, including:
* GPS Coordinates and location data
* Camera serial numbers and lens info
* Original capture timestamps
* Any existing keywords or copyright fields

### 3. Image Downsizing
We never send your full-resolution raw files or high-quality JPEGs to the AI. Images are automatically constrained to 1024px on the longest edge. This provides enough resolution for the AI to understand the scene composition, but obscures extremely fine background text or microscopic details.

### 4. Auto-Blur Human Faces (For Cloud APIs)
When you select an online cloud provider (like ChatGPT, Gemini, or Vertex AI), StyleAI offers an optional **"Blur faces"** checkbox. 
When enabled, our local face detection engine will map all human faces in the photograph and apply a heavy pixelation/blur mask to them *before* the image is encoded and sent over the internet. The cloud LLM will be instructed to ignore the blur and simply describe the rest of the scene, ensuring your subjects remain completely anonymous.

---

## Cloud Provider Privacy Policies

While we make every effort to sanitize the data sent to cloud APIs, **privacy can never be fully guaranteed when using online models**.

If you decide to use cloud APIs for their superior reasoning capabilities, please review their respective data retention policies. It is important to distinguish between consumer web interfaces (like the free ChatGPT website) and **Enterprise API keys**.

* **OpenAI (ChatGPT API):** As of March 2023, data sent to the OpenAI API is **not** used to train their models by default. [Read the OpenAI API Privacy Policy](https://openai.com/enterprise-privacy).
* **Google Gemini API / Vertex AI:** Google explicitly states that data sent to the Gemini API (paid tiers) and Google Cloud Vertex AI is **not** used to train Google's foundation models. [Read the Google Cloud Privacy Policy](https://cloud.google.com/privacy).
* **Ollama / LM Studio:** These run entirely locally on your machine. No data is sent anywhere.

We encourage you to make informed decisions about which models you use based on the sensitivity of your current photography project.
