"""
Google Vertex AI Multimodal Embeddings for images and text.
Used optionally in parallel to SigLIP2 embeddings; stored in a separate ChromaDB collection.
Config: vertex_project_id and vertex_location from Lightroom plugin or env vars.
"""
import os
from typing import List, Optional

from config import logger

# Cache: (project, location) -> model
_vertex_model_cache: dict = {}
# Last config from an index request; used by search when no explicit config is passed
_last_vertex_config: Optional[tuple] = None  # (project, location) or None
VERTEX_EMBEDDING_DIM = 1408


def _resolve_config(vertex_project_id=None, vertex_location=None):
    """Resolve project and location from options or environment."""
    project = (vertex_project_id or "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("VERTEX_PROJECT_ID")
    location = (vertex_location or "").strip() or os.environ.get("VERTEX_LOCATION", "us-central1")
    return project, location


def _get_vertex_model(vertex_project_id=None, vertex_location=None):
    """Lazy-load Vertex AI MultiModalEmbeddingModel. Config from options, last index request, or env vars."""
    global _last_vertex_config, _vertex_model_cache
    project, location = _resolve_config(vertex_project_id, vertex_location)
    if not project and _last_vertex_config:
        project, location = _last_vertex_config
    if not project:
        logger.warning("Vertex AI: Project ID not set (plugin preferences or GOOGLE_CLOUD_PROJECT); Vertex embeddings disabled.")
        return None
    key = (project, location)
    if key in _vertex_model_cache:
        return _vertex_model_cache[key]
    try:
        import vertexai
        from vertexai.vision_models import MultiModalEmbeddingModel

        # Set quota project for local ADC (gcloud auth application-default login)
        # Required by aiplatform API when using user credentials
        if "GOOGLE_CLOUD_QUOTA_PROJECT" not in os.environ:
            os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project

        vertexai.init(project=project, location=location)
        model = MultiModalEmbeddingModel.from_pretrained("multimodalembedding@001")
        _vertex_model_cache[key] = model
        _last_vertex_config = (project, location)
        logger.info("Vertex AI MultiModalEmbeddingModel initialized (project=%s, location=%s).", project, location)
        return model
    except Exception as e:
        logger.warning("Vertex AI not available: %s", e, exc_info=True)
        return None


def is_available(vertex_project_id=None, vertex_location=None) -> bool:
    """Return True if Vertex AI embeddings can be used (project configured and model loadable)."""
    return _get_vertex_model(vertex_project_id, vertex_location) is not None


def get_image_embeddings(image_bytes_list: List[bytes], vertex_project_id=None, vertex_location=None) -> List[Optional[List[float]]]:
    """
    Generate Vertex AI image embeddings for a list of images.
    One request per image (API limit). Returns one embedding per input; None on failure.
    """
    model = _get_vertex_model(vertex_project_id, vertex_location)
    if model is None:
        return [None] * len(image_bytes_list)

    try:
        from vertexai.vision_models import Image as VertexImage
    except ImportError:
        logger.warning("vertexai.vision_models not available")
        return [None] * len(image_bytes_list)

    results: List[Optional[List[float]]] = []
    for i, img_bytes in enumerate(image_bytes_list):
        try:
            vertex_image = VertexImage(image_bytes=img_bytes)
            emb_response = model.get_embeddings(
                image=vertex_image,
                dimension=VERTEX_EMBEDDING_DIM,
            )
            if emb_response and emb_response.image_embedding:
                # Normalize for cosine similarity (Chroma uses L2 distance)
                import numpy as np
                vec = np.array(emb_response.image_embedding, dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 1e-6:
                    vec = vec / norm
                results.append(vec.tolist())
            else:
                results.append(None)
        except Exception as e:
            logger.warning("Vertex embedding failed for image %s: %s", i, e, exc_info=True)
            results.append(None)
    return results


def get_text_embedding(text: str, vertex_project_id=None, vertex_location=None) -> Optional[List[float]]:
    """
    Generate Vertex AI text embedding for a search query.
    Used when searching with the Vertex collection.
    """
    model = _get_vertex_model(vertex_project_id, vertex_location)
    if model is None or not text or not text.strip():
        return None
    try:
        emb_response = model.get_embeddings(
            contextual_text=text.strip(),
            dimension=VERTEX_EMBEDDING_DIM,
        )
        if emb_response and emb_response.text_embedding:
            import numpy as np
            vec = np.array(emb_response.text_embedding, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 1e-6:
                vec = vec / norm
            return vec.tolist()
    except Exception as e:
        logger.warning("Vertex text embedding failed: %s", e, exc_info=True)
    return None
