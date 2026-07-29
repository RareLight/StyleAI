# StyleAI Core ML / ANE Feasibility Experiment

This is an isolated research tool. It is not imported by the Lightroom plugin
or Python backend, and its dependencies are not part of `server/pyproject.toml`.

The experiment answers four questions:

1. Can StyleAI's exact SigLIP2 image encoder be converted to a Core ML ML
   Program?
2. Does Core ML assign meaningful portions of the graph to the Apple Neural
   Engine?
3. How do Core ML `ALL` and `CPU_AND_NE` latency and throughput compare with
   the existing PyTorch MPS path?
4. Are the resulting normalized embeddings sufficiently equivalent for search,
   policy membership, and recommendations?

See [RESULTS.md](RESULTS.md) for the initial M2 Max pilot.

## Environment

Core ML Tools 9 officially targets PyTorch 2.7, while StyleAI currently uses a
newer PyTorch release. This experiment therefore pins its own supported PyTorch
and torchvision versions. `uv` normally reuses cached packages and model
weights, but the virtual environment remains separate.

```sh
cd experiments/coreml_ane
uv sync
```

The benchmark uses the existing Hugging Face cache by default and refuses
network access when the SigLIP2 files are absent. Add `--allow-download` only
when intentionally populating that cache.

## Convert and benchmark

```sh
uv run python benchmark.py all
```

Useful options:

```sh
uv run python benchmark.py all \
  --batch-sizes 1,8,12,16 \
  --warmups 5 \
  --repeats 25 \
  --images /path/to/a/representative/image/folder
```

Conversion artifacts are written under `artifacts/`; JSON reports are written
under `results/`. Both directories are ignored by Git.

Run phases independently when conversion is expensive:

```sh
uv run python benchmark.py convert
uv run python benchmark.py compile
uv run python benchmark.py benchmark --images /path/to/images
```

The compile phase creates a reusable `.mlmodelc`. Benchmarking automatically
prefers it over the source package so `load_seconds` reflects compiled-model
startup rather than package compilation.

For a quick feasibility pass, benchmark one Core ML mode at a time and skip
the additional compute-plan compilation:

```sh
uv run python benchmark.py benchmark \
  --batch-sizes 1 \
  --warmups 1 \
  --repeats 3 \
  --compute-units ALL \
  --skip-compute-plan
```

## Interpretation

Do not compare only the fastest individual timing. Prefer:

- median and p95 latency;
- images per second at the StyleAI hardware-tier batch sizes;
- sustained measurements after warmup;
- Core ML compute-plan evidence that operations actually prefer the ANE;
- minimum and mean cosine agreement with MPS;
- peak unified-memory and energy measurements from Instruments.

The default fidelity requirement is a minimum cosine of `0.9999`. Even when it
passes, a later integration study would also need search top-k overlap, policy
assignment agreement, recommendation precision, and the complete editing-policy
evaluation suite.

`CPU_AND_NE` is deliberately measured separately from `ALL`. `ALL` lets Core ML
partition supported operations across CPU, GPU, and ANE and may be faster.
`CPU_AND_NE` helps identify whether avoiding the GPU improves efficiency or
reduces contention, but it can be slower if important transformer operations
fall back to the CPU.

Synthetic images are suitable for compute timing. Use a representative photo
folder before drawing conclusions about embedding fidelity or production
behavior.

For sustained energy and thermal measurements, use Instruments while this
benchmark runs. The standalone process makes those measurements attributable
without involving Lightroom, Chroma, a local LLM, or the StyleAI server.
