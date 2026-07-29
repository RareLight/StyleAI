# Initial M2 Max Pilot

Run on an M2 Max with 32 GB unified memory using Core ML Tools 9.0, PyTorch
2.7.0, and the exact `timm/ViT-SO400M-16-SigLIP2-384` image encoder used by
StyleAI. Inputs were seeded synthetic images. The converted ML Program used
FP16 inputs, outputs, and compute precision.

## Results

| Measurement | PyTorch MPS | Core ML `ALL` |
| --- | ---: | ---: |
| Batch-1 median latency | 125.74 ms | 1,716.93 ms |
| Batch-1 p95 latency | 126.03 ms | 1,736.06 ms |
| Batch-1 throughput | 7.95 images/s | 0.58 images/s |

For this graph, Core ML `ALL` was approximately 13.7 times slower than MPS at
batch 1.

- Conversion succeeded and lowered all 942 traced PyTorch operations.
- Conversion took 270.21 seconds.
- The `.mlpackage` and `.mlmodelc` are each approximately 817 MiB.
- Creating the `.mlmodelc` from the package took 0.11 seconds, but first model
  load still spent 250.52 seconds in ANE device specialization.
- Embedding fidelity passed the configured gate: cosine similarity was
  `0.999977`, above the required `0.9999`.
- The compute plan contained 1,230 ML Program operations. It reported 513
  operations preferring CPU, 717 with no preferred compute device, and zero
  preferring ANE.

A full batch-size sweep was stopped after more than 20 minutes during
`CPU_AND_NE` prediction. A stack sample showed substantial BNNS and CPU
activation/cast work. The combined comparison process peaked near 14.8 GiB
because PyTorch/MPS and Core ML resources coexisted; this is not an estimate of
a single-runtime production worker.

## Interpretation

Core ML conversion is technically feasible and numerically faithful, but this
exact large SigLIP2 transformer is not an effective ANE workload in the tested
configuration. Core ML's scheduler did not prefer ANE for any reported
operation, and excluding GPU fallback caused pathological CPU-heavy execution.
There is no performance case for integrating this conversion into StyleAI.

The next useful experiments are intentionally separate:

1. Convert a fixed batch-1 graph instead of enumerated batch shapes and inspect
   placement before running a full timing sweep.
2. Test an ANE-oriented, smaller vision encoder and compare retrieval top-k,
   discovery membership, and recommendation precision—not only embedding
   latency.
3. Explore weight palettization or quantization only if placement improves,
   with the same fidelity and downstream-quality gates.
4. Use separate processes for each runtime and record peak memory, energy, and
   thermals with Instruments.

A smaller encoder would produce a different embedding space and therefore
requires a complete re-index plus downstream quality validation. It is not a
drop-in acceleration for the current StyleAI database.
