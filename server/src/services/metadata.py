"""
Service layer for metadata generation across different LLM providers.
Handles provider selection, initialization, and orchestration.
Uses lazy loading - providers are only initialized when needed.
"""

from typing import Any
from providers.base import (
    LLMProviderBase,
    MetadataGenerationRequest,
    MetadataGenerationResponse,
)
from providers.ollama import OllamaProvider
from providers.lmstudio import LMStudioProvider
from config import logger, DEFAULT_METADATA_PROVIDER
from PIL import Image
import io
import torch
import torch.nn.functional as F
from config import get_torch_device


import threading

_inference_lock = threading.Lock()


class AnalysisService:
    """
    Central service for managing metadata generation across multiple LLM providers.
    Handles provider initialization, selection, and fallback logic.
    Uses lazy loading - providers are created but models loaded only on first use.
    """

    def __init__(self, lazy_load=True):
        """
        Initialize the metadata service with all available providers.

        Args:
            lazy_load: If True, only check availability. Models are loaded on first use.
        """
        self.providers: dict[str, LLMProviderBase] = {}
        self.provider_status: dict[str, str] = {}
        self.provider_errors: dict[str, str] = {}
        self.lazy_load = lazy_load
        self._initialize_providers()

    def _initialize_providers(self):
        """
        Initialize all configured providers with lazy loading.
        Providers are created but heavy models are only loaded on first use.

        Note: Ollama and LM Studio availability is NOT checked here - they will be
        dynamically checked when listing models, allowing them to be started after server startup.
        """
        logger.info("Checking available LLM providers (lazy loading enabled)...")

        # Ollama (local) - Always register, availability checked dynamically
        try:
            ollama = OllamaProvider({})
            self.providers["ollama"] = ollama
            if ollama.is_available():
                self.provider_status["ollama"] = "available"
                logger.info("[OK] Ollama provider available")
            else:
                self.provider_status["ollama"] = "registered"
                logger.info(
                    "[INFO] Ollama provider registered (server not running, can be started later)"
                )
        except Exception as e:
            self.provider_status["ollama"] = "failed"
            self.provider_errors["ollama"] = str(e)
            logger.error(f"[FAIL] Failed to initialize Ollama provider: {e}")

        # LM Studio (local) - Always register, availability checked dynamically
        try:
            lmstudio = LMStudioProvider({})
            self.providers["lmstudio"] = lmstudio
            if lmstudio.is_available():
                self.provider_status["lmstudio"] = "available"
                logger.info("[OK] LM Studio provider initialized")
            else:
                self.provider_status["lmstudio"] = "registered"
                logger.info(
                    "[INFO] LM Studio provider registered (server not running, can be started later)"
                )
        except Exception as e:
            self.provider_status["lmstudio"] = "failed"
            self.provider_errors["lmstudio"] = str(e)
            logger.error(f"[FAIL] Failed to initialize LM Studio provider: {e}")

        if not self.providers:
            logger.error(
                "[WARN] No LLM providers available! Metadata generation will not work."
            )
        else:
            logger.info(
                f"Metadata service ready with {len(self.providers)} provider(s): {', '.join(self.providers.keys())}"
            )

    def get_available_providers(self) -> list[str]:
        """Get list of available provider names"""
        return list(self.providers.keys())

    def analyze_batch(
        self,
        image_triplets: list[tuple[bytes, str, str]],
        options: dict | list,
        image_model,
        image_processor,
        uuids_needing_embeddings=None,
        uuids_needing_metadata=None,
        exif_location_map: dict | None = None,
        pil_images: list | None = None,
    ):
        """
        Analyzes a batch of images, generating embeddings and metadata.
        Only generates data for UUIDs in the corresponding needing_* lists.

        Args:
            exif_location_map: Optional mapping of uuid → location_data dict
                (from services.exif.extract_location_tags). When provided, each
                image's metadata request gets the matching location_data injected.
            pil_images: Optional pre-decoded PIL.Image list aligned with
                image_triplets. When provided, skip re-decoding bytes for the
                CLIP preprocess path.
        """
        uuids = [triplet[1] for triplet in image_triplets]
        image_data = [triplet[0] for triplet in image_triplets]
        if pil_images is not None:
            images = pil_images
        else:
            images = [
                Image.open(io.BytesIO(data)).convert("RGB") for data in image_data
            ]

        opt = options[0] if isinstance(options, list) else options

        # If no specific UUIDs lists provided, generate for all (backward compatibility)
        if uuids_needing_embeddings is None:
            uuids_needing_embeddings = (
                uuids if opt.get("compute_embeddings", True) else []
            )
        if uuids_needing_metadata is None:
            uuids_needing_metadata = uuids if opt.get("compute_metadata", False) else []

        # Coerce to sets so the `uuid in ...` checks in the loops below are O(1)
        # regardless of what the caller passed.
        uuids_needing_embeddings = set(uuids_needing_embeddings)
        uuids_needing_metadata = set(uuids_needing_metadata)

        embeddings = None
        metadata_results = None

        if len(uuids_needing_embeddings) > 0:
            logger.debug(
                f"Generating batched embeddings for {len(uuids_needing_embeddings)} images..."
            )
            embeddings = [None] * len(uuids)
            images_to_embed = []
            valid_indices = []

            for i, uuid in enumerate(uuids):
                if uuid in uuids_needing_embeddings:
                    images_to_embed.append(images[i])
                    valid_indices.append(i)

            if images_to_embed:
                batch_embeddings = self._generate_image_embeddings(
                    images_to_embed, image_model, image_processor
                )
                for j, idx in enumerate(valid_indices):
                    embeddings[idx] = batch_embeddings[j]
        else:
            embeddings = None

        if len(uuids_needing_metadata) > 0:
            logger.info(
                f"Generating metadata for {len(uuids_needing_metadata)} images out of {len(uuids)} total"
            )

            # --- SEMANTIC CLUSTERING ---
            # Group visually similar photos to avoid redundant LLM calls
            import numpy as np
            from . import chroma as chroma_service

            cluster_mapping = {}  # maps uuid -> representative_uuid
            clusters = []  # list of dicts: {'rep_uid': uid, 'rep_emb': np.array, 'members': [uid]}
            similarity_threshold = opt.get("semantic_clustering_threshold", 0.94)

            def get_embedding(idx, uid):
                if embeddings and embeddings[idx] is not None:
                    return embeddings[idx]
                if uid not in uuids_needing_embeddings:
                    # Attempt to fetch existing embedding from ChromaDB
                    res = chroma_service.get_image(uid)
                    if (
                        res
                        and res.get("embeddings") is not None
                        and len(res["embeddings"]) > 0
                    ):
                        return res["embeddings"][0]
                return None

            for i, uid in enumerate(uuids):
                if uid not in uuids_needing_metadata:
                    continue

                emb = get_embedding(i, uid)
                if emb is None:
                    # No embedding available, cannot cluster
                    cluster_mapping[uid] = uid
                    continue

                emb_arr = np.array(emb, dtype=np.float32)
                norm = np.linalg.norm(emb_arr)
                if norm > 0:
                    emb_arr = emb_arr / norm

                found_cluster = False
                for cluster in clusters:
                    similarity = np.dot(emb_arr, cluster["rep_emb"])
                    if similarity >= similarity_threshold:
                        cluster["members"].append(uid)
                        cluster_mapping[uid] = cluster["rep_uid"]
                        found_cluster = True
                        break

                if not found_cluster:
                    clusters.append(
                        {"rep_uid": uid, "rep_emb": emb_arr, "members": [uid]}
                    )
                    cluster_mapping[uid] = uid

            rep_uids = set(cluster_mapping.values())
            logger.info(
                f"Semantic Clustering: Reduced {len(uuids_needing_metadata)} LLM calls to {len(rep_uids)} representatives (Threshold: {similarity_threshold})"
            )

            # Filter triplets to only the representatives
            filtered_triplets = []
            filtered_options = []
            for i, uuid in enumerate(uuids):
                if uuid in rep_uids:
                    filtered_triplets.append((image_data[i], uuid, ""))
                    filtered_options.append(
                        options[i] if isinstance(options, list) else options
                    )

            partial_results = self._generate_metadata_batch(
                [t[1] for t in filtered_triplets],
                [t[0] for t in filtered_triplets],
                filtered_options,
                exif_location_map=exif_location_map,
            )

            # Map partial_results back to rep_uids for cloning
            rep_results = {
                uid: res
                for uid, res in zip([t[1] for t in filtered_triplets], partial_results)
            }

            metadata_results = []
            for i, uuid in enumerate(uuids):
                if uuid in uuids_needing_metadata:
                    rep_uid = cluster_mapping.get(uuid, uuid)
                    metadata_results.append(rep_results.get(rep_uid))
                else:
                    metadata_results.append(None)
        else:
            metadata_results = None

        # Datetime/capture_time extraction is handled entirely by the client
        # (Lightroom plugin) via explicit fields in the request and stored in
        # services.index.process_image_task.
        return embeddings, metadata_results

    def _generate_image_embeddings(
        self, images: list[Image.Image], image_model, image_processor
    ) -> list[list[float] | None]:
        """
        Generates embeddings for all images in the batch concurrently on GPU.
        Errors are handled per batch.
        """
        if not image_model:
            logger.error("Vision model not initialized.")
            return [None] * len(images)

        embeddings = [None] * len(images)
        valid_indices = []
        valid_images = []

        for i, image in enumerate(images):
            if image is not None:
                valid_indices.append(i)
                valid_images.append(image)

        if not valid_images:
            return embeddings

        try:
            chunk_size = 32
            all_embeddings = []

            with torch.no_grad():
                from server_lifecycle import GLOBAL_CANCEL_EVENT

                for i in range(0, len(valid_images), chunk_size):
                    if GLOBAL_CANCEL_EVENT.is_set():
                        raise RuntimeError("Batch canceled by watchdog.")
                    chunk_images = valid_images[i : i + chunk_size]

                    # Interleave CPU preprocessing to allow other threads to use the GPU/GIL
                    # while this thread is doing CPU-bound image transformations.
                    tensors = [image_processor(img) for img in chunk_images]
                    chunk = torch.stack(tensors).to(get_torch_device())

                    # Acquire lock to serialize forward passes and prevent VRAM multiplier effect
                    with _inference_lock:
                        image_features = image_model.encode_image(chunk)

                    normalized = F.normalize(image_features, p=2, dim=1)
                    # .cpu() blocks this thread and releases the GIL until the GPU finishes.
                    # This allows other Waitress threads to run their CPU preprocessing concurrently!
                    all_embeddings.extend(normalized.cpu().numpy().tolist())

                for j, idx in enumerate(valid_indices):
                    embeddings[idx] = all_embeddings[j]

        except Exception as e:
            logger.error(
                f"Failed to generate batched image embeddings: {e}",
                exc_info=True,
            )

        return embeddings

    def _generate_metadata_batch(
        self,
        uuids: list[str],
        image_data: list[bytes],
        options: dict | list,
        exif_location_map: dict | None = None,
    ) -> list[MetadataGenerationResponse | None]:
        """
        Generates metadata for all images in the batch concurrently.
        """
        from concurrent.futures import ThreadPoolExecutor

        results = [None] * len(uuids)
        from server_lifecycle import GLOBAL_CANCEL_EVENT

        def process_single(i, uuid):
            if GLOBAL_CANCEL_EVENT.is_set():
                raise RuntimeError("Batch canceled by watchdog.")
            opt = options[i] if isinstance(options, list) else options
            # Inject per-image EXIF location data without mutating the options dict
            if exif_location_map and uuid in exif_location_map:
                per_image_options = dict(opt)
                per_image_options["location_data"] = exif_location_map[uuid]
            else:
                per_image_options = opt

            return i, self.generate_metadata_single(
                uuid, image_data[i], per_image_options
            )

        # ThreadPoolExecutor is safe here since LLM calls are I/O bound
        # Max workers matches batch size to process the entire batch in parallel
        max_workers = max(1, len(uuids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(process_single, i, uuid) for i, uuid in enumerate(uuids)
            ]
            for future in futures:
                try:
                    idx, response = future.result()
                    results[idx] = response
                except Exception as e:
                    logger.error(
                        f"Error in concurrent metadata generation: {e}", exc_info=True
                    )

        return results

    def generate_metadata_single(
        self, uuid: str, image_data: bytes, options: dict
    ) -> MetadataGenerationResponse:
        """
        Generate metadata for a single image.
        """
        provider = options.get("provider") or DEFAULT_METADATA_PROVIDER

        if provider not in self.providers:
            if not self.providers:
                return MetadataGenerationResponse(
                    uuid=uuid, success=False, error="No LLM providers available"
                )
            provider = list(self.providers.keys())[0]
            warning_msg = (
                f"Requested provider not available, using fallback: {provider}"
            )
            logger.warning(warning_msg)

        selected_provider = self.providers[provider]
        logger.info(f"Generating metadata for {uuid} using {provider}")

        request = MetadataGenerationRequest(
            image_data=image_data,
            uuid=uuid,
            provider=provider,
            model=options["model"],
            generate_keywords=options["generate_keywords"],
            generate_caption=options["generate_caption"],
            generate_title=options["generate_title"],
            generate_alt_text=options["generate_alt_text"],
            language=options["language"],
            temperature=options["temperature"],
            max_tokens=options.get("max_tokens"),
            user_prompt=options.get("user_prompt"),
            submit_keywords=options["submit_keywords"],
            submit_folder_names=options["submit_folder_names"],
            existing_keywords=options.get("existing_keywords"),
            location_data=options.get("location_data"),
            folder_names=options.get("folder_names"),
            user_context=options.get("user_context"),
            keyword_categories=options.get("keyword_categories"),
            bilingual_keywords=options.get("bilingual_keywords", False),
            keyword_secondary_language=options.get("keyword_secondary_language"),
            generate_aliases=options.get("generate_aliases", False),
            catalog_keywords=options.get("catalog_keywords"),
            system_prompt=options.get("prompt"),
            date_time=options.get("date_time"),
            ollama_base_url=options.get("ollama_base_url"),
            lmstudio_base_url=options.get("lmstudio_base_url"),
        )

        # Diagnostic logging for prompt context
        ctx_summary = []
        if request.existing_keywords:
            ctx_summary.append(f"{len(request.existing_keywords)} keywords")
        if request.folder_names:
            ctx_summary.append(f"{len(request.folder_names)} folders")
        if request.user_context:
            ctx_summary.append(f"context ({len(str(request.user_context))} chars)")
        if request.location_data:
            ctx_summary.append("Location")
        if ctx_summary:
            logger.info(f"Context for {uuid}: {', '.join(ctx_summary)}")
        else:
            logger.debug(f"No additional context for {uuid}")

        import time

        response = None
        for attempt in range(2):
            try:
                response = selected_provider.generate_metadata(request)
                if response.success:
                    if "warning_msg" in locals():
                        response.warning = warning_msg
                    return response
                logger.warning(
                    f"[Attempt {attempt + 1}/2] Failed to generate metadata for {uuid}: {response.error}"
                )
            except Exception as e:
                logger.warning(
                    f"[Attempt {attempt + 1}/2] Unexpected error during metadata generation for {uuid}: {e}",
                    exc_info=(attempt == 1),
                )
                if attempt == 1:
                    return MetadataGenerationResponse(
                        uuid=uuid, success=False, error=str(e)
                    )

            if attempt < 1:
                time.sleep(2)

        # If we exhausted attempts and have a response object with an error
        if response:
            if "warning_msg" in locals():
                response.warning = warning_msg
            logger.error(
                f"[FAIL] Failed to generate metadata for {uuid}: {response.error}"
            )
            return response

        return MetadataGenerationResponse(
            uuid=uuid, success=False, error="Unknown error"
        )

    def get_available_models(
        self,
        ollama_base_url: str | None = None,
        lmstudio_base_url: str | None = None,
    ) -> dict[str, list[str]]:
        """
        Return all available multimodal (vision-capable) models from all providers.
        """
        result: dict[str, list[str]] = {}
        for provider_name, provider_instance in self.providers.items():
            try:
                if provider_name == "ollama" and ollama_base_url:
                    provider_instance = OllamaProvider({"base_url": ollama_base_url})
                if provider_name == "lmstudio" and lmstudio_base_url:
                    # Reuse existing provider instance but point it to a different host
                    provider_instance.host = lmstudio_base_url

                if not provider_instance.is_available():
                    result[provider_name] = []
                    continue

                models = provider_instance.list_available_models()
                result[provider_name] = models
            except Exception as e:
                logger.error(
                    f"Error listing models for provider {provider_name}: {e}",
                    exc_info=True,
                )
                result[provider_name] = []
        return result

    def get_health_status(self) -> dict[str, Any]:
        """Return health status of LLM providers."""
        return {
            "llm_providers": self.provider_status,
            "llm_errors": self.provider_errors,
        }


# Global service instance
_analysis_service: AnalysisService | None = None


def get_analysis_service() -> AnalysisService:
    """
    Get or create the global analysis service instance.
    """
    global _analysis_service
    if _analysis_service is None:
        _analysis_service = AnalysisService()
    return _analysis_service
