import re

from version_info import BACKEND_BUILD, BACKEND_RELEASE_TAG, BACKEND_VERSION


import os

import psutil
import subprocess


def get_backend_version_info() -> dict:
    cpu_cores = os.cpu_count() or 4
    gpu_cores = 0
    total_ram_gb = psutil.virtual_memory().total / (1024**3)
    gpu_type = "cpu"
    vram_gb = 0

    try:
        import torch

        if torch.cuda.is_available():
            gpu_type = "cuda"
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        elif torch.backends.mps.is_available():
            gpu_type = "mps"
            vram_gb = total_ram_gb  # Unified memory
            try:
                output = subprocess.check_output(
                    ["system_profiler", "SPDisplaysDataType"], text=True
                )
                for line in output.split("\n"):
                    if "Total Number of Cores" in line:
                        gpu_cores = int(line.split(":")[1].strip())
                        break
            except Exception:
                pass
    except ImportError:
        pass

    recommended = 3
    if gpu_type == "cuda":
        if vram_gb >= 16 and total_ram_gb >= 32:
            recommended = 8
        elif vram_gb >= 8 and total_ram_gb >= 16:
            recommended = 6
        else:
            recommended = 4
    elif gpu_type == "mps":
        if gpu_cores >= 30 and total_ram_gb >= 30:
            recommended = 8
        elif gpu_cores >= 14 and total_ram_gb >= 16:
            recommended = 6
        elif gpu_cores >= 8:
            recommended = 4
        else:
            recommended = 3
    else:
        if cpu_cores >= 16:
            recommended = 4
        elif cpu_cores >= 8:
            recommended = 3
        else:
            recommended = 2

    return {
        "backend_version": BACKEND_VERSION,
        "backend_release_tag": BACKEND_RELEASE_TAG,
        "backend_build": BACKEND_BUILD,
        "recommended_parallel_tasks": recommended,
        "hardware_profile": {
            "cpu_cores": cpu_cores,
            "gpu_cores": gpu_cores,
            "gpu_type": gpu_type,
            "total_ram_gb": round(total_ram_gb, 1),
            "vram_gb": round(vram_gb, 1),
        },
    }


def check_plugin_backend_version(
    plugin_version: str | None,
    plugin_build: int | None = None,
    plugin_release_tag: str | None = None,
) -> dict:
    backend = get_backend_version_info()
    normalized_backend = _normalize_version(backend["backend_version"])
    normalized_plugin = _normalize_version(plugin_version)

    if not normalized_plugin:
        return {
            **backend,
            "plugin_version": plugin_version,
            "plugin_release_tag": plugin_release_tag,
            "plugin_build": plugin_build,
            "compatible": False,
            "reason": "plugin_version is missing or invalid",
        }

    # Dev fallback:
    # Local development uses placeholder versions in Info.lua and backend defaults.
    # Allow this combination so development setups are not blocked.
    if _is_dev_backend(
        backend["backend_version"], backend["backend_release_tag"]
    ) and _is_default_dev_plugin(normalized_plugin):
        return {
            **backend,
            "plugin_version": plugin_version,
            "plugin_release_tag": plugin_release_tag,
            "plugin_build": plugin_build,
            "compatible": True,
            "reason": "dev fallback: placeholder plugin version accepted for dev backend",
        }

    compatible = normalized_plugin == normalized_backend
    reason = (
        "exact version match" if compatible else "plugin and backend version differ"
    )

    return {
        **backend,
        "plugin_version": plugin_version,
        "plugin_release_tag": plugin_release_tag,
        "plugin_build": plugin_build,
        "compatible": compatible,
        "reason": reason,
    }


def _normalize_version(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if candidate.startswith("v"):
        candidate = candidate[1:]
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", candidate)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"


def _is_dev_backend(version: str | None, release_tag: str | None) -> bool:
    v = (version or "").lower()
    t = (release_tag or "").lower()
    return ("dev" in v) or ("dev" in t)


def _is_default_dev_plugin(normalized_plugin_version: str | None) -> bool:
    return normalized_plugin_version == "9.9.9"
