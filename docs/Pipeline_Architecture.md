# StyleAI: Analysis & Editing Pipelines

This document outlines the core data pipelines in StyleAI, detailing how photos flow from Lightroom through our local and cloud AI models, what data is passed into them, and what they output.

## Visual Flowchart

```mermaid
graph TD
    %% Styling
    classDef client fill:#f9d0c4,stroke:#333,stroke-width:2px;
    classDef api fill:#fff3cd,stroke:#333,stroke-width:2px;
    classDef ai fill:#d1e7dd,stroke:#333,stroke-width:2px;
    classDef db fill:#cff4fc,stroke:#333,stroke-width:2px;
    classDef logic fill:#e2e3e5,stroke:#333,stroke-width:2px;

    %% Ingestion Pipeline
    subgraph Ingestion Pipeline
        LR1(Lightroom:<br>Render JPEG & EXIF):::client --> API1([POST /index_base64]):::api
        API1 --> CPU1[CPU Preprocessing:<br>Hash & Decode]:::logic
        CPU1 --> SIG1[SigLIP2 Visual Embedding]:::ai
        CPU1 --> FACE[InsightFace Detection]:::ai
        CPU1 --> SEM[LLM Semantic Tagging]:::ai
        SIG1 --> C_MAIN[(ChromaDB:<br>images_siglip)]:::db
        FACE --> S_MAIN[(SQLite:<br>metadata)]:::db
        SEM --> S_MAIN
    end

    %% Training Pipeline
    subgraph Style Training Pipeline
        LR2(Lightroom:<br>Extract Recipe & JPEG):::client --> API2([POST /training/add]):::api
        API2 --> SIG2[SigLIP2 Visual Embedding]:::ai
        API2 --> SEM2[LLM Semantic Tagging]:::ai
        SIG2 --> C_TRAIN[(ChromaDB:<br>edit_training)]:::db
        SEM2 --> S_TRAIN[(SQLite:<br>style_profiles)]:::db
        LR2 -.->|Edit Recipe| S_TRAIN
    end

    %% Editing Pipeline
    subgraph AI Editing Pipeline
        LR3(Lightroom:<br>Target JPEG & Intent):::client --> API3([POST /edit_base64]):::api
        API3 --> SIG3[SigLIP2 Visual Embedding]:::ai
        SIG3 --> ENGINE[Style Engine]:::logic
        ENGINE --> QUERY[Query ChromaDB for Matches]:::logic
        QUERY --> C_TRAIN
        C_TRAIN -.->|Top-K Matches| EVAL{Confidence > Threshold?}:::logic
        
        EVAL -->|Yes| MATH[LLM-Free Path:<br>Math Interpolation &<br>RAW Compensation]:::logic
        EVAL -->|No| LLM[LLM Fallback Path:<br>Few-Shot Generative Prompt]:::ai
        
        MATH --> RECIPE[Final Edit Recipe JSON]:::api
        LLM --> RECIPE
        RECIPE --> LR4(Lightroom:<br>Apply Sliders):::client
    end
```

---

## 1. Photo Analysis & Indexing Pipeline (Ingestion)

Triggered when a user imports or indexes photos (`TaskAnalyzeAndIndex.lua`). This pipeline builds the foundational search index and metadata repository.

### Data Flow & Stages
1. **Lightroom (Client)**: 
   - Renders a lightweight, temporary JPEG of the photo.
   - Extracts EXIF data (capture time, camera model, lens) and existing Lightroom keywords.
   - Sends this payload (Base64 JPEG + JSON metadata) to `POST /index_base64`.
2. **CPU Preprocessing (`services/index.py`)**: 
   - Background threads decode the JPEG, calculate a stable MD5 hash for deduplication, and prepare tensors.
3. **Local Visual Embedding (SigLIP2 via `services/clip.py`)**: 
   - **Input**: Preprocessed image tensor.
   - **Output**: 1152-dimensional dense visual embedding.
4. **Local Face Detection & Embedding (InsightFace via `services/face.py`)**:
   - **Input**: Preprocessed image tensor.
   - **Output**: Bounding boxes, 512-dimensional face embeddings, and estimated age/gender metadata.
5. **Semantic Tagging (LLM via `services/metadata.py`)**:
   - **Input**: Base64 image and a strict System Prompt enforcing JSON output.
   - **Prompt**: Instructs the LLM (Gemini/ChatGPT/Local) to act as a professional photo editor and extract a predefined JSON schema containing arrays of keywords, main genre, lighting condition, and a descriptive summary.
   - **Output**: A structured JSON object containing semantic metadata.
6. **Storage (`services/chroma.py`)**:
   - Embeddings are pushed to ChromaDB collections (`images_siglip`, `faces`).
   - JSON metadata and EXIF data are attached as metadata payloads to the vector records and optionally stored in SQLite.

---

## 2. Style Training Pipeline

Triggered via `TaskTrainFromEdits.lua`. This pipeline allows the system to learn the user's personal grading style.

### Data Flow & Stages
1. **Lightroom (Client)**:
   - Evaluates the user's selected photo.
   - Extracts the final Lightroom Develop settings (the "Edit Recipe").
   - Sends the JPEG + Recipe to the backend.
2. **Embedding & Semantic Extraction**:
   - Runs through the exact same SigLIP2 and LLM Semantic Tagging steps as the standard Indexing pipeline.
3. **Storage (`services/training.py` & `services/style_catalog.py`)**:
   - **Input**: The visual embedding, the semantic metadata (lighting, genre), and the Lightroom Edit Recipe.
   - **Output**: The record is saved into a specialized `edit_training` ChromaDB collection and the SQLite `style_profiles` table. This builds a searchable database of the user's grading decisions mapped to specific lighting and composition contexts.

---

## 3. AI Editing Pipeline

Triggered via `TaskAiEditPhotos.lua`. This pipeline generates a custom edit recipe for a target photo based on learned styles or generative prompts.

### Data Flow & Stages
1. **Lightroom (Client)**:
   - Renders a temporary JPEG of the unedited target photo.
   - Gathers EXIF data, user intent strings ("Make it moody"), and AI slider constraints (e.g., "Don't touch Color Grading").
   - Sends to `POST /edit_base64`.
2. **Target Evaluation**:
   - The backend runs the target photo through SigLIP2 to get its visual embedding.
3. **Style Retrieval & Scoring (`services/style_engine.py`)**:
   - **Input**: Target photo's SigLIP2 embedding.
   - **Query**: ChromaDB's `edit_training` collection is queried for the top-N visually similar training examples.
   - **Scoring**: Candidates are re-ranked using a composite score based on exposure proximity (luminance/contrast match), scene-type overlap (genre match), and time-of-day proximity.
4. **Recipe Generation (Two Paths)**:
   - **Path A: LLM-Free Interpolation (High Confidence)**:
     - **Input**: The top-K retrieved training examples and their Edit Recipes.
     - **Execution**: A mathematical interpolation of the slider values weighted by the composite match score, followed by a RAW-adaptive compensation layer to adjust for exposure differences between the training images and the target image.
     - **Output**: The final interpolated Edit Recipe JSON.
   - **Path B: Generative LLM (Low Confidence or Fallback)**:
     - *See "LLM Fallback Deep Dive" below.*
5. **Lightroom Execution (`DevelopEditManager.lua`)**:
   - The backend returns the JSON recipe to the Lua plugin.
   - The plugin maps the JSON keys to Lightroom's internal Develop parameters (e.g., `Exposure2012`, `Contrast2012`) and applies them to the RAW file.

---

## 4. LLM Fallback Deep Dive

When the Style Engine evaluates a target photo but cannot find a sufficiently close match in the `edit_training` database (i.e., confidence score falls below the threshold), it relies on the generative capabilities of vision-language models (e.g., Gemini 1.5 Pro or GPT-4o).

Instead of producing a generic auto-edit, the pipeline dynamically injects the top retrieved (but low-confidence) training examples as **few-shot context** into the LLM prompt. This forces the LLM to extrapolate the user's general grading philosophy rather than relying on its base weights.

### Prompt Construction Payload
When executing the fallback, the `providers/base.py` module constructs a prompt comprising the following elements:

1. **System Instruction**:
   > "You are an expert, professional Lightroom colorist. Your goal is to analyze the provided image and generate a complete Lightroom edit recipe. You must format your output perfectly adhering to the provided JSON schema."
2. **Target Image**: Base64 encoded JPEG.
3. **Target EXIF Data**: Camera Model, Lens, ISO, Aperture, Shutter Speed (crucial for the LLM to understand sensor dynamic range and noise floor).
4. **User Intent & Constraints**: 
   - Intent: e.g., "Warm and vintage cinematic look."
   - Constraints: Instructing the LLM which JSON keys it is *forbidden* from generating (e.g., if the user unchecked "Apply Auto Crop" or "Adjust White Balance" in the Lightroom UI).
5. **Few-Shot Examples (The Context Injection)**:
   - The prompt dynamically appends the top 3-5 matches from the `edit_training` database, formatted as:
   ```json
   {
     "example_1": {
       "image_description": "A sunny portrait taken at 35mm ISO 100",
       "user_edit_recipe": {
         "Exposure2012": 0.45,
         "Contrast2012": 15,
         "Highlights2012": -50,
         "Shadows2012": 25,
         ...
       }
     }
   }
   ```
6. **Output Requirement**: The model is instructed to output the final recipe matching the exact slider ranges valid in Adobe Lightroom (e.g., -100 to +100 for Basic Tone sliders, -5 to +5 for Exposure).
