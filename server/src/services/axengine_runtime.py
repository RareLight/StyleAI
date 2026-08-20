"""Bounded AX model discovery and ownership-safe local server supervision."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

import psutil
import requests

import config
from config import AX_ENGINE_BASE_URL, AX_ENGINE_MODEL_ROOT, logger


MAX_SCAN_DEPTH = 4
MAX_SCAN_DIRECTORIES = 512
MAX_METADATA_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class AXModelCandidate:
    key: str
    path: Path
    display_name: str
    model_type: str
    architecture: str | None
    quantization: str | None
    manifest_schema: str
    vision_configured: bool

    def descriptor(self) -> dict[str, Any]:
        qualifiers = [
            value for value in (self.quantization, "MLX", "AX Engine") if value
        ]
        return {
            "key": self.key,
            "label": f"{self.display_name} — {' · '.join(qualifiers)}",
            "display_name": self.display_name,
            "provider": "axengine",
            "format": "mlx",
            "model_family": self.model_type or None,
            "architecture": self.architecture,
            "quantization": self.quantization,
            "vision": self.vision_configured,
            "native_multimodal": None,
            "resident": False,
            "loadable": True,
            "speculation_kind": "runtime_managed",
        }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _quantization_label(config_data: dict[str, Any]) -> str | None:
    quantization = config_data.get("quantization")
    if not isinstance(quantization, dict):
        quantization = config_data.get("quantization_config")
    if not isinstance(quantization, dict):
        return None
    bits = quantization.get("bits")
    mode = str(quantization.get("mode") or "").strip().upper()
    if isinstance(bits, int) and bits > 0:
        return f"{mode + ' ' if mode else ''}{bits}-bit"
    method = quantization.get("quant_method")
    return str(method).strip().upper() if method else None


def discover_ax_models(root: str | os.PathLike[str]) -> list[AXModelCandidate]:
    """Discover AX-native vision candidates without following escaped symlinks."""
    raw_root = Path(root).expanduser()
    if not raw_root.is_absolute():
        raise ValueError("AX Engine model root must be an absolute path")
    try:
        canonical_root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("AX Engine model root is unavailable") from exc
    if not canonical_root.is_dir():
        raise ValueError("AX Engine model root is not a directory")

    candidates: list[AXModelCandidate] = []
    directory_count = 0
    for current, directories, _files in os.walk(canonical_root, followlinks=False):
        directory_count += 1
        if directory_count > MAX_SCAN_DIRECTORIES:
            break
        current_path = Path(current)
        depth = len(current_path.relative_to(canonical_root).parts)
        if depth >= MAX_SCAN_DEPTH:
            directories[:] = []
        safe_directories = []
        for name in sorted(directories):
            child = current_path / name
            try:
                resolved = child.resolve(strict=True)
                resolved.relative_to(canonical_root)
            except (OSError, RuntimeError, ValueError):
                continue
            if not child.is_symlink():
                safe_directories.append(name)
        directories[:] = safe_directories

        manifest = _read_json(current_path / "model-manifest.json")
        config_data = _read_json(current_path / "config.json")
        if not manifest or not config_data:
            continue
        if manifest.get("schema_version") != "ax.native_model.v1":
            continue
        vision_configured = isinstance(config_data.get("vision_config"), dict)
        if not vision_configured:
            continue
        digest = hashlib.sha256(str(current_path).encode("utf-8")).hexdigest()[:16]
        architectures = config_data.get("architectures")
        architecture = (
            str(architectures[0])
            if isinstance(architectures, list) and architectures
            else None
        )
        candidates.append(
            AXModelCandidate(
                key=f"axlocal-{digest}",
                path=current_path,
                display_name=current_path.name,
                model_type=str(
                    config_data.get("model_type") or manifest.get("model_family") or ""
                ),
                architecture=architecture,
                quantization=_quantization_label(config_data),
                manifest_schema="ax.native_model.v1",
                vision_configured=True,
            )
        )
        directories[:] = []
    return sorted(candidates, key=lambda item: item.display_name.lower())


class AXEngineRuntime:
    """Manage only the AX process this backend instance launched."""

    def __init__(self, model_root: str = AX_ENGINE_MODEL_ROOT):
        self._lock = threading.RLock()
        self._model_root = model_root
        self._process: subprocess.Popen[bytes] | None = None
        self._process_create_time: float | None = None
        self._executable: str | None = None
        self._log_handle = None
        self._candidate_to_resident: dict[str, str] = {}
        self._session = requests.Session()
        self._session.trust_env = False

    def configure_model_root(self, model_root: str | None) -> None:
        if model_root is None:
            return
        # Validate before replacing a working configuration.
        discover_ax_models(model_root)
        with self._lock:
            self._model_root = str(Path(model_root).expanduser())

    def candidates(self) -> list[AXModelCandidate]:
        return discover_ax_models(self._model_root)

    @contextmanager
    def inference_guard(self):
        """Prevent model stop/restart for the full duration of an AX request."""
        with self._lock:
            yield

    def candidate_for_key(self, key: str) -> AXModelCandidate | None:
        return next(
            (candidate for candidate in self.candidates() if candidate.key == key), None
        )

    def resident_mapping(self) -> dict[str, str]:
        with self._lock:
            return dict(self._candidate_to_resident)

    def _health(self) -> dict[str, Any] | None:
        try:
            response = self._session.get(
                f"{AX_ENGINE_BASE_URL}/health",
                timeout=(0.75, 1.5),
                allow_redirects=False,
            )
            if response.status_code != 200:
                return None
            value = response.json()
            return (
                value
                if isinstance(value, dict)
                and value.get("service") == "ax-engine-server"
                else None
            )
        except (requests.RequestException, ValueError):
            return None

    def is_server_available(self) -> bool:
        return self._health() is not None

    def ownership(self) -> str:
        if not self.is_server_available():
            return "stopped"
        with self._lock:
            return "owned" if self._owned_process_is_valid() else "external"

    def is_configured(self) -> bool:
        if self.is_server_available():
            return True
        try:
            return self.find_binary() is not None and bool(self.candidates())
        except ValueError:
            return False

    @staticmethod
    def find_binary() -> str | None:
        candidates = [
            shutil.which("ax-engine"),
            "/opt/homebrew/bin/ax-engine",
            "/usr/local/bin/ax-engine",
        ]
        for candidate in candidates:
            if (
                candidate
                and Path(candidate).is_file()
                and os.access(candidate, os.X_OK)
            ):
                return str(Path(candidate).resolve())
        return None

    def _owned_process_is_valid(self) -> bool:
        process = self._process
        if (
            process is None
            or process.poll() is not None
            or self._process_create_time is None
        ):
            return False
        try:
            observed = psutil.Process(process.pid)
            return (
                abs(observed.create_time() - self._process_create_time) < 0.01
                and str(Path(observed.exe()).resolve()) == self._executable
            )
        except (psutil.Error, OSError):
            return False

    def _log_path(self) -> Path:
        if config.DB_PATH:
            return Path(config.DB_PATH) / "ax-engine.log"
        return Path("/tmp") / f"styleai-ax-engine-{os.getpid()}.log"

    def _resident_cards(self) -> list[dict[str, Any]]:
        response = self._session.get(
            f"{AX_ENGINE_BASE_URL}/v1/models",
            timeout=(2.0, 5.0),
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        cards = payload.get("data") if isinstance(payload, dict) else None
        return [card for card in cards or [] if isinstance(card, dict)]

    def start(self, candidate: AXModelCandidate) -> str:
        with self._lock:
            if self.is_server_available():
                if not self._owned_process_is_valid():
                    raise RuntimeError(
                        "AX Engine is running externally; load this model outside StyleAI"
                    )
                resident = self._candidate_to_resident.get(candidate.key)
                if resident:
                    return resident
                # AX unload is soft and may retain weights. Restart the process so
                # exactly one selected model is resident and memory is reclaimed.
                self.stop()

            binary = self.find_binary()
            if not binary:
                raise RuntimeError("AX Engine is not installed or executable")
            log_path = self._log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = log_path.open("ab", buffering=0)
            environment = dict(os.environ)
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "AX_ENGINE_API_KEY",
            ):
                environment.pop(key, None)
            environment["NO_PROXY"] = "127.0.0.1,localhost"
            command = [
                binary,
                "serve",
                str(candidate.path),
                "--host",
                "127.0.0.1",
                "--port",
                "31418",
                "--offline",
            ]
            logger.info("Starting managed AX Engine for selected local model")
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                env=environment,
                close_fds=True,
            )
            self._executable = str(Path(binary).resolve())
            self._process_create_time = psutil.Process(self._process.pid).create_time()

            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    self._clear_owned_process()
                    raise RuntimeError("Managed AX Engine exited during model startup")
                if self.is_server_available():
                    cards = self._resident_cards()
                    eligible = [card for card in cards if card.get("id")]
                    if len(eligible) == 1:
                        resident_id = str(eligible[0]["id"])
                        self._candidate_to_resident[candidate.key] = resident_id
                        return resident_id
                time.sleep(0.25)
            self.stop()
            raise RuntimeError(
                "Managed AX Engine did not become ready within 5 minutes"
            )

    def ensure_model(self, selected_key: str) -> str:
        candidate = self.candidate_for_key(selected_key)
        if candidate is None:
            # Exact resident IDs are valid only when advertised by the server.
            cards = self._resident_cards()
            resident_ids = {str(card.get("id")) for card in cards}
            if len(cards) == 1 and selected_key in resident_ids:
                return selected_key
            if selected_key in resident_ids:
                raise RuntimeError(
                    "External AX Engine must have exactly one model resident"
                )
            raise RuntimeError("Selected AX Engine model is no longer available")
        return self.start(candidate)

    def _clear_owned_process(self) -> None:
        self._process = None
        self._process_create_time = None
        self._executable = None
        self._candidate_to_resident.clear()
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def stop(self, wait_seconds: float = 10.0) -> bool:
        with self._lock:
            if not self._owned_process_is_valid():
                return False
            process = self._process
            assert process is not None
            process.terminate()
            try:
                process.wait(timeout=max(0.0, wait_seconds))
            except subprocess.TimeoutExpired:
                if self._owned_process_is_valid():
                    process.kill()
                    process.wait(timeout=2.0)
            self._clear_owned_process()
            return True

    def request_owned_stop(self) -> bool:
        """Signal an owned child without waiting; used during hard backend exit."""
        with self._lock:
            if not self._owned_process_is_valid():
                return False
            assert self._process is not None
            self._process.terminate()
            return True


_runtime: AXEngineRuntime | None = None
_runtime_lock = threading.Lock()


def get_axengine_runtime() -> AXEngineRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = AXEngineRuntime()
        return _runtime
