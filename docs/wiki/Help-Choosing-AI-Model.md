# Help: Choosing an AI Model

> The exact model lists exposed by the plugin come from the backend at runtime.
> The names below reflect the curated lists shipped with the current backend
> (`server/src/providers/`). Pricing and availability change over time — verify
> with each provider before relying on production cost estimates.

## Decision factors

Choose based on:

- privacy requirements
- quality expectations (description detail, keyword accuracy, edit recipe sanity)
- runtime per image and batch throughput
- available local hardware (VRAM/RAM, Apple Silicon vs. discrete GPU)

## Local models

Local providers run on your own machine, so privacy is the strongest argument
for using them. Quality of small open-weights vision models has improved
significantly; choose a capable local vision model that fits your available unified memory or VRAM.

### Ollama

Install and start Ollama from [ollama.com](https://ollama.com/), then pull at
least one vision-capable model. Recommended starting points:

```bash
ollama pull qwen3-vl:4b-instruct-q4_K_M     # fast, ~6 GB VRAM
ollama pull qwen3-vl:8b-instruct-q4_K_M     # better quality, ~10 GB VRAM
ollama pull gemma3:4b-it-q4_K_M             # good general default
ollama pull gemma3:12b-it-q4_K_M            # higher quality if you have VRAM
ollama pull llava                            # legacy fallback
```

Browse all vision models: [ollama.com/search?c=vision](https://ollama.com/search?c=vision).
See [Ollama Setup](Help-Ollama-Setup).

### LM Studio

Download from [lmstudio.ai](https://lmstudio.ai/download), enable server mode,
and download one or more vision models from inside the app. Recommended:

- `qwen/qwen3-vl-4b` — fast baseline.
- `qwen/qwen3-vl-8b` — better description quality.
- `google/gemma3-4b` / `google/gemma3-12b` — strong general-purpose options.

On Apple Silicon prefer the **MLX** variants of the same model — they run
significantly faster than the GGUF builds. See [LM Studio Setup](Help-LM-Studio-Setup).

## Quick recommendations

| Workflow                              | Suggested first try                              |
| ------------------------------------- | ------------------------------------------------ |
| Privacy-first / no API billing        | Ollama `qwen3-vl:8b` or LM Studio `qwen3-vl-8b`  |
| Apple Silicon, local                  | LM Studio MLX build of `qwen3-vl` or `gemma3`    |

## Practical recommendation

The dropdown in *Analyze & Index* always reflects what the
backend currently advertises — newer models that ship with future backend
updates will appear automatically. If a model you expect is missing, check
that the corresponding local server is configured and reachable
from the backend (the *Plugin Manager → Status* section reports availability
per provider).

When evaluating, run the same batch of 10–20 representative photos through
two candidates and compare:

- keyword coverage and accuracy
- description quality and language correctness
- runtime per image and end-to-end batch time
- system load
