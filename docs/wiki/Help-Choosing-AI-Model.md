# Choosing a Local Metadata Model

The model selector in **Prepare Photos → Metadata Settings** lists models from
Ollama and LM Studio running on the same computer. These models generate text
metadata only; they do not train or apply learned edits.

Choose a vision-capable, instruction-following model that fits comfortably in
available VRAM or unified memory. A model that triggers swap usually has worse
end-to-end throughput than a smaller model. Compare candidates on a fixed,
representative photo set using:

- primary-subject and species/object accuracy;
- unsupported-detail and background-noise rate;
- keyword specificity and duplication;
- title, caption, and alt-text usefulness;
- structured-response reliability;
- cold-start latency, per-photo latency, and peak memory.

Start with a current 4B-class quantized vision model on modest hardware and an
8B–12B-class model only when measured memory headroom is comfortable. Model
names and quantizations change frequently, so use the provider's current vision
model catalog rather than treating a hard-coded name as a compatibility list.

On Apple Silicon, compare LM Studio MLX and Ollama/GGUF variants on the actual
machine. The fastest runtime is not always the best metadata producer, and
local LLM requests are intentionally serialized to avoid GPU context switching
and unified-memory thrashing.

See [Ollama Setup](Help-Ollama-Setup) and [LM Studio Setup](Help-LM-Studio-Setup).
