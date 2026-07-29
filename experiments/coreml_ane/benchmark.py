"""Isolated SigLIP2 MPS versus Core ML / ANE feasibility benchmark."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import statistics
import sys
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

DEFAULT_MODEL_NAME = "ViT-SO400M-16-SigLIP2-384"
DEFAULT_MODEL_ID = f"timm/{DEFAULT_MODEL_NAME}"
DEFAULT_BATCH_SIZES = (1, 8, 12, 16)
DEFAULT_COMPUTE_UNITS = ("ALL", "CPU_AND_NE")


def parse_batch_sizes(value: str | Iterable[int]) -> tuple[int, ...]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        try:
            sizes = tuple(int(item) for item in items)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "batch sizes must be comma-separated integers"
            ) from exc
    else:
        sizes = tuple(int(item) for item in value)
    if not sizes or any(size <= 0 for size in sizes) or len(set(sizes)) != len(sizes):
        raise argparse.ArgumentTypeError("batch sizes must be unique positive integers")
    return sizes


def parse_compute_units(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        units = tuple(item.strip().upper() for item in value.split(",") if item.strip())
    else:
        units = tuple(str(item).strip().upper() for item in value)
    supported = {"ALL", "CPU_AND_NE"}
    if (
        not units
        or len(set(units)) != len(units)
        or any(unit not in supported for unit in units)
    ):
        choices = ", ".join(sorted(supported))
        raise argparse.ArgumentTypeError(
            f"compute units must be unique values selected from: {choices}"
        )
    return units


def latency_summary(seconds: list[float], batch_size: int) -> dict[str, float]:
    if not seconds or batch_size <= 0:
        raise ValueError("latencies and batch_size must be positive")
    ordered = sorted(float(value) for value in seconds)
    if any(not math.isfinite(value) or value <= 0 for value in ordered):
        raise ValueError("latencies must be finite and positive")
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    median = statistics.median(ordered)
    return {
        "median_ms": median * 1000.0,
        "p95_ms": ordered[p95_index] * 1000.0,
        "minimum_ms": ordered[0] * 1000.0,
        "images_per_second": batch_size / median,
    }


def fidelity_summary(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    minimum_cosine: float,
) -> dict[str, float | bool]:
    expected = np.asarray(reference, dtype=np.float64)
    actual = np.asarray(candidate, dtype=np.float64)
    if expected.shape != actual.shape or expected.ndim != 2:
        raise ValueError("fidelity arrays must be matching 2D matrices")
    expected_norms = np.linalg.norm(expected, axis=1, keepdims=True)
    actual_norms = np.linalg.norm(actual, axis=1, keepdims=True)
    if np.any(expected_norms <= 0) or np.any(actual_norms <= 0):
        raise ValueError("fidelity arrays must contain non-zero rows")
    expected = expected / expected_norms
    actual = actual / actual_norms
    cosines = np.sum(expected * actual, axis=1)
    return {
        "minimum_cosine": float(np.min(cosines)),
        "mean_cosine": float(np.mean(cosines)),
        "maximum_absolute_error": float(np.max(np.abs(expected - actual))),
        "passed": bool(float(np.min(cosines)) >= minimum_cosine),
    }


@dataclass(frozen=True)
class ModelLocation:
    model_name: str
    model_id: str
    weights_path: str
    config_path: str


def _locate_model(
    model_name: str,
    model_id: str,
    *,
    allow_download: bool,
) -> ModelLocation:
    from huggingface_hub import hf_hub_download

    weights = hf_hub_download(
        repo_id=model_id,
        filename="open_clip_model.safetensors",
        local_files_only=not allow_download,
    )
    config = hf_hub_download(
        repo_id=model_id,
        filename="open_clip_config.json",
        local_files_only=not allow_download,
    )
    return ModelLocation(model_name, model_id, weights, config)


def _load_open_clip(location: ModelLocation, device: str):
    import open_clip

    model, _, processor = open_clip.create_model_and_transforms(
        location.model_name,
        pretrained=location.weights_path,
    )
    model.eval()
    model.to(device)
    return model, processor


def _synthetic_images(count: int, seed: int):
    from PIL import Image

    rng = np.random.default_rng(seed)
    return [
        Image.fromarray(
            rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8),
            mode="RGB",
        )
        for _ in range(count)
    ]


def _load_images(image_directory: Path | None, count: int, seed: int):
    if image_directory is None:
        return _synthetic_images(count, seed)
    from PIL import Image

    extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
    paths = sorted(
        path
        for path in image_directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in extensions
    )
    if not paths:
        raise ValueError(f"no supported images found in {image_directory}")
    images = []
    for index in range(count):
        with Image.open(paths[index % len(paths)]) as image:
            images.append(image.convert("RGB").copy())
    return images


def _preprocess_batches(processor, images, batch_sizes: tuple[int, ...]):
    import torch

    tensors = [processor(image) for image in images]
    return {
        batch_size: torch.stack(tensors[:batch_size]).contiguous()
        for batch_size in batch_sizes
    }


class _NormalizedImageEncoder:
    """Delay the torch dependency until conversion actually runs."""

    @staticmethod
    def wrap(model):
        import torch
        from torch.nn import functional

        class Encoder(torch.nn.Module):
            def __init__(self, source):
                super().__init__()
                self.source = source

            def forward(self, images):
                return functional.normalize(
                    self.source.encode_image(images),
                    p=2,
                    dim=1,
                )

        return Encoder(model).eval()


def convert_model(
    *,
    output_path: Path,
    model_name: str,
    model_id: str,
    batch_sizes: tuple[int, ...],
    allow_download: bool,
) -> dict[str, Any]:
    import coremltools as ct
    import torch

    location = _locate_model(model_name, model_id, allow_download=allow_download)
    source_model, _ = _load_open_clip(location, "cpu")
    wrapper = _NormalizedImageEncoder.wrap(source_model)
    example = torch.zeros((1, 3, 384, 384), dtype=torch.float32)
    with torch.inference_mode():
        traced = torch.jit.trace(wrapper, example, strict=False)
        traced = torch.jit.freeze(traced.eval())
        reference = traced(example).detach().cpu().numpy()

    shapes = ct.EnumeratedShapes(
        shapes=[(size, 3, 384, 384) for size in batch_sizes],
        default=(1, 3, 384, 384),
    )
    started = perf_counter()
    converted = ct.convert(
        traced,
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.macOS15,
        inputs=[
            ct.TensorType(
                name="image",
                shape=shapes,
                dtype=np.float16,
            )
        ],
        outputs=[ct.TensorType(name="embedding", dtype=np.float16)],
        compute_precision=ct.precision.FLOAT16,
        compute_units=ct.ComputeUnit.ALL,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    converted.author = "StyleAI isolated ANE feasibility experiment"
    converted.short_description = (
        "SigLIP2 normalized image embeddings; not used by the StyleAI runtime"
    )
    converted.user_defined_metadata["styleai_model_id"] = model_id
    converted.user_defined_metadata["styleai_batch_sizes"] = ",".join(
        str(size) for size in batch_sizes
    )
    converted.save(str(output_path))
    conversion_seconds = perf_counter() - started
    del converted, traced, wrapper, source_model
    gc.collect()
    return {
        "model": asdict(location),
        "output_path": str(output_path),
        "batch_sizes": list(batch_sizes),
        "conversion_seconds": conversion_seconds,
        "torch_zero_reference_norm": float(np.linalg.norm(reference)),
    }


def compile_model_artifact(
    model_path: Path,
    compiled_path: Path,
) -> dict[str, Any]:
    import coremltools as ct

    if compiled_path.exists():
        if compiled_path.stat().st_mtime >= model_path.stat().st_mtime:
            return {
                "source_path": str(model_path),
                "compiled_path": str(compiled_path),
                "compile_seconds": 0.0,
                "reused": True,
            }
        raise FileExistsError(
            f"stale compiled model exists; remove it before recompiling: {compiled_path}"
        )
    started = perf_counter()
    result_path = ct.models.utils.compile_model(
        str(model_path),
        destination_path=str(compiled_path),
    )
    return {
        "source_path": str(model_path),
        "compiled_path": str(result_path),
        "compile_seconds": perf_counter() - started,
        "reused": False,
    }


def _mps_synchronize() -> None:
    import torch

    if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()


def _benchmark_mps(
    model,
    batches,
    *,
    warmups: int,
    repeats: int,
) -> tuple[dict[str, Any], dict[int, np.ndarray]]:
    import torch

    encoder = _NormalizedImageEncoder.wrap(model).to("mps").eval()
    results = {}
    reference_outputs = {}
    with torch.inference_mode():
        for batch_size, cpu_batch in batches.items():
            batch = cpu_batch.to("mps")
            for _ in range(warmups):
                encoder(batch)
                _mps_synchronize()
            timings = []
            output = None
            for _ in range(repeats):
                started = perf_counter()
                output = encoder(batch)
                _mps_synchronize()
                timings.append(perf_counter() - started)
            if output is None:
                raise RuntimeError("MPS benchmark produced no output")
            reference_outputs[batch_size] = output.float().cpu().numpy()
            results[str(batch_size)] = latency_summary(timings, batch_size)
            del batch, output
    del encoder
    gc.collect()
    return results, reference_outputs


def _benchmark_coreml_unit(
    model_path: Path,
    compiled_path: Path | None,
    batches,
    references,
    *,
    unit_name: str,
    warmups: int,
    repeats: int,
    minimum_cosine: float,
) -> dict[str, Any]:
    import coremltools as ct

    compute_unit = getattr(ct.ComputeUnit, unit_name)
    descriptor = ct.models.MLModel(str(model_path), skip_model_load=True)
    spec = descriptor.get_spec()
    input_name = spec.description.input[0].name
    output_name = spec.description.output[0].name
    started = perf_counter()
    if compiled_path is None:
        model = ct.models.MLModel(str(model_path), compute_units=compute_unit)
    else:
        model = ct.models.CompiledMLModel(
            str(compiled_path),
            compute_units=compute_unit,
        )
    load_seconds = perf_counter() - started
    batch_results = {}
    for batch_size, tensor in batches.items():
        values = tensor.numpy().astype(np.float16, copy=False)
        for _ in range(warmups):
            model.predict({input_name: values})
        timings = []
        output = None
        for _ in range(repeats):
            started = perf_counter()
            output = model.predict({input_name: values})[output_name]
            timings.append(perf_counter() - started)
        if output is None:
            raise RuntimeError(f"Core ML {unit_name} benchmark produced no output")
        batch_results[str(batch_size)] = {
            **latency_summary(timings, batch_size),
            "fidelity": fidelity_summary(
                references[batch_size],
                np.asarray(output),
                minimum_cosine=minimum_cosine,
            ),
        }
    del model
    gc.collect()
    return {
        "compute_unit": unit_name,
        "load_seconds": load_seconds,
        "batches": batch_results,
    }


def inspect_compute_plan(model_path: Path) -> dict[str, Any]:
    import coremltools as ct

    try:
        compiled_path = (
            str(model_path)
            if model_path.suffix == ".mlmodelc"
            else ct.models.utils.compile_model(str(model_path))
        )
        plan = ct.models.compute_plan.MLComputePlan.load_from_path(
            path=compiled_path,
            compute_units=ct.ComputeUnit.ALL,
        )
        main_function = plan.model_structure.program.functions["main"]
        device_counts: dict[str, int] = {}
        operation_count = 0
        for operation in main_function.block.operations:
            operation_count += 1
            usage = plan.get_compute_device_usage_for_mlprogram_operation(operation)
            preferred = getattr(usage, "preferred_compute_device", None)
            label = type(preferred).__name__ if preferred is not None else "Unknown"
            device_counts[label] = device_counts.get(label, 0) + 1
        return {
            "available": True,
            "compiled_path": str(compiled_path),
            "operation_count": operation_count,
            "preferred_device_operation_counts": device_counts,
        }
    except Exception as exc:  # noqa: BLE001 - unsupported plans are benchmark data
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def benchmark_model(
    *,
    model_path: Path,
    compiled_path: Path | None,
    model_name: str,
    model_id: str,
    batch_sizes: tuple[int, ...],
    image_directory: Path | None,
    warmups: int,
    repeats: int,
    minimum_cosine: float,
    allow_download: bool,
    seed: int,
    compute_units: tuple[str, ...],
    include_compute_plan: bool,
) -> dict[str, Any]:
    import coremltools as ct
    import psutil
    import torch

    if not torch.backends.mps.is_available():
        raise RuntimeError("PyTorch MPS is unavailable on this machine")
    location = _locate_model(model_name, model_id, allow_download=allow_download)
    model, processor = _load_open_clip(location, "cpu")
    images = _load_images(image_directory, max(batch_sizes), seed)
    batches = _preprocess_batches(processor, images, batch_sizes)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    mps_results, references = _benchmark_mps(
        model,
        batches,
        warmups=warmups,
        repeats=repeats,
    )
    del model, processor, images
    gc.collect()
    if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()

    coreml_results = {}
    failures = {}
    for unit_name in compute_units:
        try:
            coreml_results[unit_name] = _benchmark_coreml_unit(
                model_path,
                compiled_path,
                batches,
                references,
                unit_name=unit_name,
                warmups=warmups,
                repeats=repeats,
                minimum_cosine=minimum_cosine,
            )
        except Exception as exc:  # noqa: BLE001 - compare independent compute units
            failures[unit_name] = f"{type(exc).__name__}: {exc}"

    del batches, references
    gc.collect()
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "system": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "coremltools": ct.__version__,
            "physical_memory_gib": psutil.virtual_memory().total / (1024**3),
        },
        "model": asdict(location),
        "model_path": str(model_path),
        "runtime_model_path": str(compiled_path or model_path),
        "batch_sizes": list(batch_sizes),
        "warmups": warmups,
        "repeats": repeats,
        "minimum_cosine_required": minimum_cosine,
        "mps": {"compute_unit": "MPS_GPU", "batches": mps_results},
        "coreml": coreml_results,
        "coreml_failures": failures,
        "compute_plan": (
            inspect_compute_plan(compiled_path or model_path)
            if include_compute_plan
            else {"available": False, "skipped": True}
        ),
        "process_rss_delta_mib": ((process.memory_info().rss - rss_before) / (1024**2)),
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def _record_failure(path: Path, phase: str):
    try:
        yield
    except Exception as exc:
        _write_json(
            path,
            {
                "status": "failed",
                "phase": phase,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "timestamp_utc": datetime.now(UTC).isoformat(),
            },
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Isolated SigLIP2 Core ML / ANE feasibility benchmark."
    )
    parser.add_argument(
        "command",
        choices=("convert", "compile", "benchmark", "all"),
        help="Convert, compile, benchmark, or run all phases.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts"),
    )
    parser.add_argument("--results", type=Path, default=Path("results/latest.json"))
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--batch-sizes",
        type=parse_batch_sizes,
        default=DEFAULT_BATCH_SIZES,
    )
    parser.add_argument("--images", type=Path)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument(
        "--compute-units",
        type=parse_compute_units,
        default=DEFAULT_COMPUTE_UNITS,
        help="Comma-separated Core ML modes: ALL,CPU_AND_NE.",
    )
    parser.add_argument(
        "--skip-compute-plan",
        action="store_true",
        help="Skip the additional model compilation used for placement inspection.",
    )
    parser.add_argument("--minimum-cosine", type=float, default=0.9999)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow Hugging Face access when the StyleAI model is not cached.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.warmups < 0 or args.repeats <= 0:
        raise SystemExit("warmups must be non-negative and repeats must be positive")
    model_path = args.artifact_dir / "StyleAI-SigLIP2-Image.mlpackage"
    compiled_path = args.artifact_dir / "StyleAI-SigLIP2-Image.mlmodelc"
    conversion_report = args.results.with_name(args.results.stem + "-conversion.json")
    compilation_report = args.results.with_name(args.results.stem + "-compilation.json")
    if args.command in {"convert", "all"}:
        with _record_failure(conversion_report, "conversion"):
            conversion = convert_model(
                output_path=model_path,
                model_name=args.model_name,
                model_id=args.model_id,
                batch_sizes=args.batch_sizes,
                allow_download=args.allow_download,
            )
            _write_json(
                conversion_report,
                {"status": "ok", "conversion": conversion},
            )
    if args.command in {"compile", "all"}:
        if not model_path.exists():
            raise SystemExit(f"Core ML model does not exist: {model_path}")
        with _record_failure(compilation_report, "compilation"):
            compilation = compile_model_artifact(model_path, compiled_path)
            _write_json(
                compilation_report,
                {"status": "ok", "compilation": compilation},
            )
    if args.command in {"benchmark", "all"}:
        if not model_path.exists():
            raise SystemExit(f"Core ML model does not exist: {model_path}")
        with _record_failure(args.results, "benchmark"):
            report = benchmark_model(
                model_path=model_path,
                compiled_path=compiled_path if compiled_path.exists() else None,
                model_name=args.model_name,
                model_id=args.model_id,
                batch_sizes=args.batch_sizes,
                image_directory=args.images,
                warmups=args.warmups,
                repeats=args.repeats,
                minimum_cosine=args.minimum_cosine,
                allow_download=args.allow_download,
                seed=args.seed,
                compute_units=args.compute_units,
                include_compute_plan=not args.skip_compute_plan,
            )
            _write_json(args.results, {"status": "ok", "benchmark": report})
            print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
