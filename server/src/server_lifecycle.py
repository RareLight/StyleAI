"""
Robust State & Lifecycle Management Service.

Controls the startup signaling, heartbeat, and teardown of the Python backend.
Uses PID tracking and `.OK` files to coordinate gracefully with the Lightroom
plugin, avoiding zombie processes. Includes an automatic VRAM idle-unload mechanism
to free GPU resources after a period of inactivity.
"""

import os
import time
import config
from config import logger, IMAGE_MODEL_ID, CLIP_MODEL_NAME, TORCH_DEVICE
import open_clip
from utils.open_clip_compat import wrap_tokenizer
import threading
import datetime
import gc
import torch
from huggingface_hub import hf_hub_download
from services import face as service_face
from services import chroma as service_chroma


# Lazy-loadable global model instances
# model, processor and tokenizer start as None and will be loaded on first use.
model = None
processor = None  # This will hold the image preprocessor
tokenizer = None
_model_load_error = None

# Idle-unload handling
# If the model hasn't been used for this many seconds, it will be unloaded to free memory.
IDLE_UNLOAD_SECONDS = 10 * 60  # 10 minutes

# Server idle-shutdown: if no HTTP request arrives in this many seconds, the whole
# server process exits gracefully.  Overridable via env var.
IDLE_SHUTDOWN_SECONDS = int(os.environ.get("STYLEAI_IDLE_SHUTDOWN_SECONDS", "600"))

_last_used = None
_last_request_time = time.time()  # track last HTTP request for server idle shutdown
_model_lock = threading.RLock()
_unloader_thread = None


def _get_open_clip_tokenizer(local_files_only=False):
    """Resolve tokenizer via open_clip's built-in config.

    Uses CLIP_MODEL_NAME (the architecture name) rather than IMAGE_MODEL_ID
    (the HF repo "timm/..."): the architecture name resolves to a built-in
    config and returns the proper HFTokenizer (Gemma) for SigLIP2. Passing
    "timm/..." with no schema prefix silently falls back to SimpleTokenizer
    inside open_clip without raising, which would yield incorrect embeddings.
    """
    if local_files_only:
        return open_clip.get_tokenizer(CLIP_MODEL_NAME, local_files_only=True)
    return open_clip.get_tokenizer(CLIP_MODEL_NAME)


def _set_last_used():
    global _last_used
    _last_used = datetime.datetime.utcnow()


def _needs_unload():
    if model is None and processor is None and tokenizer is None:
        return False
    if _last_used is None:
        return False
    delta = datetime.datetime.utcnow() - _last_used
    return delta.total_seconds() >= IDLE_UNLOAD_SECONDS


def is_model_cached() -> bool:
    """Check if the model is bundled or cached locally without downloading."""
    global model
    with _model_lock:
        if model is not None:
            return True

        try:
            cached_model_file = hf_hub_download(
                repo_id=IMAGE_MODEL_ID,
                filename="open_clip_model.safetensors",
                local_files_only=True,
            )
            cached_model_dir = os.path.dirname(cached_model_file)
            if os.path.isdir(cached_model_dir):
                config_file = os.path.join(cached_model_dir, "open_clip_config.json")
                weights_file = os.path.join(
                    cached_model_dir, "open_clip_model.safetensors"
                )
                if os.path.isfile(config_file) and os.path.isfile(weights_file):
                    return True
            return False
        except Exception:
            return False


def load_model():
    """Load the OpenCLIP model (idempotent)."""
    global model, processor, tokenizer, _model_load_error
    with _model_lock:
        if model is not None:
            _set_last_used()
            return

        try:
            logger.info("Trying to load open_clip model from local cache")

            try:
                cached_model_file = hf_hub_download(
                    repo_id=IMAGE_MODEL_ID,
                    filename="open_clip_model.safetensors",
                    local_files_only=True,
                )

                cached_model_dir = os.path.dirname(cached_model_file)

                logger.info(f"Checking for cached model at: {cached_model_dir}")

                # Check if local model directory exists (production/bundled scenario)
                if os.path.isdir(cached_model_dir):
                    # Verify model files exist
                    config_file = os.path.join(
                        cached_model_dir, "open_clip_config.json"
                    )
                    weights_file = os.path.join(
                        cached_model_dir, "open_clip_model.safetensors"
                    )

                    if os.path.isfile(config_file) and os.path.isfile(weights_file):
                        logger.info(
                            f"Loading OpenCLIP model from cached directory: {cached_model_dir}"
                        )

                        # Preferred path for pip-installed open_clip_torch:
                        # use known architecture name + local checkpoint path.
                        try:
                            model_obj, _, proc = open_clip.create_model_and_transforms(
                                CLIP_MODEL_NAME,
                                pretrained=weights_file,
                            )
                            tok = _get_open_clip_tokenizer(local_files_only=True)
                        except Exception:
                            # Backward compatibility for older vendored open_clip forks.
                            local_model_uri = f"local-dir:{cached_model_dir}"
                            model_obj, _, proc = open_clip.create_model_and_transforms(
                                local_model_uri, pretrained=None
                            )
                            try:
                                tok = open_clip.get_tokenizer(
                                    local_model_uri, local_files_only=True
                                )
                            except Exception:
                                tok = _get_open_clip_tokenizer(local_files_only=True)

                        _set_last_used()
                        logger.info("Loaded OpenCLIP model (lazy)")
                    else:
                        logger.warning(
                            "Bundled model directory exists but required files missing"
                        )
                        logger.warning(
                            f"Config file exists: {os.path.isfile(config_file)}"
                        )
                        logger.warning(
                            f"Weights file exists: {os.path.isfile(weights_file)}"
                        )
                        raise FileNotFoundError("Bundled model files incomplete")

                    try:
                        model_obj.to(TORCH_DEVICE)
                        logger.info(f"Text and vision model moved to {TORCH_DEVICE}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to move text and vision model to {TORCH_DEVICE}: {e}."
                        )

                    model = model_obj
                    processor = proc
                    tokenizer = wrap_tokenizer(tok)

            except Exception as e:
                logger.warning(
                    f"Failed to load OpenCLIP model from local cache. This can happen if the model is not fully downloaded, is corrupted, or if there is a configuration issue. The error was: {e}",
                    exc_info=True,
                )
                logger.info("Falling back to loading OpenCLIP model via hf-hub")
                model_obj, _, proc = open_clip.create_model_and_transforms(
                    CLIP_MODEL_NAME,
                    pretrained=f"hf-hub:{IMAGE_MODEL_ID}",
                )
                tok = _get_open_clip_tokenizer()
                try:
                    model_obj.to(TORCH_DEVICE)
                    logger.info(f"Text and vision model moved to {TORCH_DEVICE}")
                except Exception as move_exc:
                    logger.warning(
                        f"Failed to move text and vision model to {TORCH_DEVICE}: {move_exc}."
                    )

                model = model_obj
                processor = proc
                tokenizer = wrap_tokenizer(tok)
                _set_last_used()
                logger.info("Loaded OpenCLIP model via hf-hub fallback")

        except Exception as e:
            _model_load_error = str(e)
            logger.exception(f"Failed to load OpenCLIP model (lazy): {e}")
            raise


def unload_model():
    """Unload the loaded model to free GPU/CPU memory."""
    global model, processor, tokenizer
    with _model_lock:
        if model is None and processor is None and tokenizer is None:
            return

        logger.info("Unloading OpenCLIP model due to inactivity...")
        try:
            # If the model is a torch module, try moving it to cpu and delete the reference.
            try:
                if hasattr(model, "to"):
                    model.to("cpu")
            except Exception:
                pass

            model = None
            processor = None
            tokenizer = None
            _last_used = None

            # Best-effort free memory for CUDA
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

            # Force a GC pass
            gc.collect()
            logger.info("Unloaded OpenCLIP model.")
        except Exception as e:
            logger.warning(f"Error while unloading model: {e}")


def unload_all_resources():
    """Unload all heavy models and resources from memory."""
    logger.info("Unloading all resources (CLIP, InsightFace, ChromaDB)...")

    # 1. CLIP (OpenCLIP)
    try:
        unload_model()
    except Exception as e:
        logger.error(f"Failed to unload CLIP model: {e}")

    # 2. InsightFace
    try:
        service_face.unload_face_app()
    except Exception as e:
        logger.error(f"Failed to unload InsightFace model: {e}")

    # 3. ChromaDB
    try:
        service_chroma.unload_collections()
    except Exception as e:
        logger.error(f"Failed to unload ChromaDB: {e}")

    # 4. Final GC
    import gc

    gc.collect()
    logger.info("All resources unloaded successfully.")


def note_request():
    """Called by Flask before_request to record that the server is active."""
    global _last_request_time
    _last_request_time = time.time()


def _idle_unloader_loop():
    """Background thread which periodically checks whether the model should be unloaded."""
    global _unloader_thread
    logger.info(
        "Starting model idle monitor (unload after %ss)",
        IDLE_UNLOAD_SECONDS,
    )
    try:
        while True:
            time.sleep(60)
            try:
                if _needs_unload():
                    unload_model()
            except Exception:
                logger.exception("Error in idle monitor background thread")
    finally:
        logger.info("Idle monitor thread exiting")


def _ensure_unloader_thread():
    global _unloader_thread
    if _unloader_thread is None or not _unloader_thread.is_alive():
        _unloader_thread = threading.Thread(
            target=_idle_unloader_loop, daemon=True, name="server_lifecycle_unloader"
        )
        _unloader_thread.start()


def get_model():
    """Return the model, loading it lazily if needed."""
    load_model()
    _ensure_unloader_thread()
    return model


def get_processor():
    load_model()
    _ensure_unloader_thread()
    return processor


def get_tokenizer():
    load_model()
    _ensure_unloader_thread()
    return tokenizer


def get_db_dir():
    return os.path.dirname(config.DB_PATH) if config.DB_PATH else None


def write_pid_file():
    db_dir = get_db_dir()
    if not db_dir:
        return
    pid_file = os.path.join(db_dir, "styleai-server.pid")
    tmp_file = pid_file + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(str(os.getpid()))
    os.replace(tmp_file, pid_file)  # atomic on POSIX and Windows


def remove_pid_file():
    db_dir = get_db_dir()
    if not db_dir:
        return
    pid_file = os.path.join(db_dir, "styleai-server.pid")
    try:
        os.remove(pid_file)
    except FileNotFoundError:
        pass


def write_ok_file():
    db_dir = get_db_dir()
    if not db_dir:
        return
    ok_file = os.path.join(db_dir, "styleai-server.OK")
    with open(ok_file, "w") as f:
        f.write("OK\n")


def remove_ok_file():
    db_dir = get_db_dir()
    if not db_dir:
        return
    ok_file = os.path.join(db_dir, "styleai-server.OK")
    try:
        os.remove(ok_file)
    except FileNotFoundError:
        pass


def request_shutdown():
    """Gracefully-but-firmly shut down the server process.

    Uses a daemon thread so the current HTTP response can finish being sent
    before the process exits.  os._exit(0) is used instead of SIGINT because
    waitress's signal handler and shell job control can swallow/intercept SIGINT
    in background processes, leaving a zombie server on port 19819.
    """
    logger.info("Shutdown request received")

    def _exit_after_delay():
        time.sleep(1)  # Allow the /shutdown HTTP response to flush
        remove_pid_file()
        remove_ok_file()
        logger.info("Bye.")
        # Hard exit — skips Python-level finally blocks but guarantees the
        # process actually terminates (waitress doesn't always honour SIGINT).
        os._exit(0)

    threading.Thread(target=_exit_after_delay, daemon=True).start()


def get_health_status():
    """Return health status of the model."""
    status = "not_loaded"
    if model is not None:
        status = "loaded"
    elif _model_load_error is not None:
        status = "failed"

    return {"clip_model": status, "clip_error": _model_load_error}
