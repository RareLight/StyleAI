# Local Model Discovery and Display Labels Implementation Checklist

This checklist covers richer local-model labels for Lightroom's metadata-model
selectors. The callable provider/model key remains distinct from its
user-facing label so publisher and quantization details never alter inference
routing or saved preferences.

## Contract

- [x] Return `/models` provider entries as descriptors with stable `key` and
      `label` fields plus provider-reported identity metadata when available.
- [x] Keep current model keys as popup values and benchmark request model IDs.
- [x] Use the descriptor contract consistently in Lightroom without a second
      legacy StyleAI response shape.
- [x] Continue applying LM Studio's vision-capability filter.

## LM Studio discovery

- [x] Continue using the scoped LM Studio SDK model catalog as the authoritative
      set of callable vision-model keys.
- [x] Query LM Studio's loopback-only native `GET /api/v1/models` endpoint for
      publisher, quantization, selected variant, format, and parameter metadata.
- [x] Disable redirects and environment proxy use for the native metadata
      request so discovery cannot leave the local machine.
- [x] Match native descriptors to SDK keys without replacing the SDK key used
      for inference.
- [x] Fall back to SDK display name, format, parameter size, path, model key,
      and file size when the native endpoint is unavailable or authenticated.
- [x] Do not infer a model's quantization from ambiguous marketing names.

## Lightroom UI and benchmark

- [x] Format concise labels such as
      `Qwen2.5-VL 7B — Q4_K_M · GGUF · lmstudio-community`.
- [x] Use the same normalized choices in Prepare Photos and the developer LLM
      benchmark.
- [x] Preserve the exact provider/model key in benchmark manifests and calls.
- [x] Keep normal text-only speculative-decoding draft models out of the list
      through the existing vision-capability filter.

## Tests and documentation

- [x] Test native metadata enrichment, variant matching, SDK-only fallback,
      vision filtering, and loopback request safeguards.
- [x] Test the enriched `/models` response shape.
- [x] Pin the Lua enriched-choice contract.
- [x] Document displayed identity fields and LM Studio fallback behavior.
- [x] Run focused provider, service, route, and Lua contract tests.
- [x] Run Python lint/format checks, Lightroom plug-in validation, Lua syntax
      validation, and `git diff --check`.

## Validation record

- Focused provider/service/route/Lua contract suite: 51 passed.
- Complete backend suite: 553 passed; one unrelated Chroma restore test failed
  in the aggregate run and passed immediately in isolation.
- Ruff format and lint: passed across `server/src` and `server/test`.
- Python bytecode compilation, Lightroom plug-in validation, all Lua syntax
  checks, and `git diff --check`: passed.
