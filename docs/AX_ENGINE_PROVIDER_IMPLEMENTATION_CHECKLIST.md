# AX Engine Local Provider Implementation Checklist

This checklist covers an explicit third local metadata provider backed by an
optional, locally installed AX Engine runtime. StyleAI may attach to an existing
loopback instance or launch and manage an instance itself. It preserves StyleAI's
local-only boundary: photo proxies and metadata may be sent only to a validated
loopback AX Engine endpoint, never to AX Serving, LAN discovery targets, or a
hosted API.

## Implementation status (2026-08-17)

The first complete implementation slice is in place and covered by automated
tests:

- [x] AX Engine is an explicit, mutually exclusive metadata provider alongside
      Disabled, Ollama, and LM Studio.
- [x] Plug-In Manager stores the active provider and the machine-local AX model
      root; ordinary tagging still selects one model at operation time and the
      benchmark still supports a successive multi-model selection.
- [x] Candidate discovery is bounded to AX-native vision packages beneath the
      configured root and does not load models merely to populate the selector.
- [x] Managed AX Engine starts lazily on loopback with `--offline`; StyleAI never
      installs AX Engine, downloads models, or takes ownership of an external
      process.
- [x] The managed runtime serializes launch, inference, and shutdown. Exactly one
      AX model may be resident: changing models stops and verifies the owned child
      before launching the next package.
- [x] AX image inference fails closed unless the resident model card explicitly
      advertises native multimodal image input.
- [x] Active-provider filtering and mismatch rejection prevent cross-provider
      fallback for both ordinary tagging and benchmarks.
- [x] Provider, scanner, runtime, route, service, benchmark, and Lua contract
      tests are present; the full backend suite, Ruff, translation sync, and
      plug-in validation pass.

The remaining items below are follow-up hardening or live validation, principally
the persistent crash-recovery ownership marker, a dedicated Help diagnostic, and
a user-approved real-image/Lightroom smoke test.

## Feasibility and verified local state

- [x] Confirm AX Engine exposes the required provider-neutral endpoints:
      `GET /health`, `GET /api/version`, `GET /v1/runtime`, `GET /v1/models`, and
      `POST /v1/chat/completions`.
- [x] Confirm native vision-capable sessions accept OpenAI-style inline JPEG/PNG
      `image_url` content parts using base64 `data:` URIs.
- [x] Confirm `/v1/models` is authoritative for all resident model IDs and
      per-model image/chat capabilities, so one StyleAI provider instance can
      discover and route to multiple loaded models.
- [x] Confirm AX Engine supports multi-model serving, but adopt a stricter StyleAI
      product constraint: only one AX model may be resident at a time.
- [x] Probe the user's live server read-only. AX Engine 7.1.0 is healthy at
      `127.0.0.1:31418`, uses the native MLX path on an Apple M2 Max, and currently
      advertises one resident model: `gemma-4-26b-a4b-it-assistant-mtp`.
- [x] Record the current blocker: that resident model advertises
      `capabilities.input.image=false` and
      `native_multimodal_input_supported=false`. StyleAI must hide it from the
      vision-model selector and refuse image metadata inference through it.
- [x] Confirm no new Python dependency is required; StyleAI already ships
      `requests` and AX Engine exposes an HTTP contract.
- [x] Confirm the installed CLI's local launch contract:
      `ax-engine serve <model-path> --host <host> --port <port> --offline`.
- [x] Inspect the configured development model root,
      `/Volumes/Thunderbolt/Models`. It currently contains three AX-native MLX
      packages with `ax.native_model.v1` manifests and vision configurations,
      plus another MLX VLM without an AX-native manifest and several GGUF trees.
- [x] Confirm the AX-native candidates are individually large (about 15–18 GB),
      so discovery must not imply automatic simultaneous residency.

## Architectural decisions

- [ ] Update the repository product-boundary documentation from “Ollama and LM
      Studio adapters only” to explicitly permit AX Engine as a third loopback,
      open-weights provider.
- [ ] Add `AXEngineProvider` under `server/src/providers`; do not route AX Engine
      through the Ollama adapter merely because AX also exposes Ollama-shaped
      compatibility endpoints. Its OpenAI-shaped API and runtime metadata are the
      more precise contract.
- [ ] Keep the default endpoint fixed to `http://127.0.0.1:31418`. If an
      environment override is supported, validate the resolved host as loopback
      and reject credentials, remote hosts, redirects off loopback, LAN discovery,
      AX Serving, and arbitrary URLs.
- [ ] Keep AX Engine an optional user-installed dependency. StyleAI may discover
      the binary, launch it on demand, and manage only a process it created; it
      must never install or upgrade AX Engine or download/delete model artifacts.
- [ ] Support two explicit lifecycle modes: attach to a healthy compatible AX
      Engine already listening on `127.0.0.1:31418` as **external/unmanaged**, or
      launch an **owned/managed** instance when the port is free. Never replace,
      restart, signal, or otherwise take ownership of an external instance.
- [ ] Discover candidate packages under a configured local root, initially
      `/Volumes/Thunderbolt/Models`, but load only the selected working set. Do
      not start AX Engine or load every discovered package merely to populate a
      model selector.
- [ ] Treat AX Engine's memory preflight and model-family allowlist as
      authoritative. Do not duplicate its memory formulas or infer compatibility
      from model names.
- [ ] Document that AX Engine unload/idle eviction soft-parks generations and may
      retain their weights; restarting AX Engine is the deterministic way to
      reclaim parked-model memory.
- [ ] Do not add API-key storage in the initial integration. A server configured
      with authentication should report an actionable unsupported-configuration
      error rather than prompting StyleAI to persist a secret.
- [ ] Define exactly one active metadata API provider at a time. Provider choice
      is global plug-in/runtime configuration, not a per-photo, per-operation, or
      per-model choice.
- [ ] Remove provider fallback. If the active provider or selected model is
      unavailable, fail clearly and preserve the user's choice; never send a
      photo to the first registered provider or silently cross an API boundary.

## Plug-In Manager provider configuration

- [ ] Add a **Local metadata API** popup to Plug-In Manager → Status & Setup with
      `Disabled`, `Ollama`, `LM Studio`, and `AX Engine`. This is the single source
      of truth for ordinary tagging, model discovery, health summaries, and the
      developer benchmark.
- [ ] Store the active provider in Lightroom's plug-in preferences (machine-wide,
      not in the catalog-local database). Keep provider-specific model choices so
      switching away and back can restore the last valid model for that provider.
- [ ] Filter normal model selectors and the benchmark checklist to the active
      provider only. Do not permit one benchmark run to combine Ollama, LM Studio,
      and AX Engine models; comparisons remain multiple reports/runs with explicit
      provider identity.
- [ ] Label every model with its provider where results or persisted reports can
      outlive the current setting, even though only one provider is selectable at
      runtime.
- [ ] Show provider-specific configuration directly beneath the popup: Ollama and
      LM Studio setup/status help, or AX Engine binary status, managed/external
      ownership, model root, discovered/resident counts, and restart/reclaim state.
      Hide irrelevant provider controls when another provider is active.
- [ ] On provider change, clear any incompatible active `provider::model` binding,
      draft-model selection, and cached inventory while retaining the prior choice
      in that provider's own preference slot.
- [ ] Serialize provider changes through the local-LLM admission lane. Reject or
      defer a switch while metadata/benchmark work is active. Switching away from
      AX Engine stops an owned instance only after the lane drains; external AX,
      Ollama, and LM Studio processes remain untouched.
- [ ] Synchronize the Lightroom preference into the backend's runtime state using
      a narrow loopback configuration endpoint with an explicit provider enum.
      Reject inference and model-load requests whose provider does not match the
      active runtime provider.
- [ ] Make `Disabled` intentional and non-erroring: embedding/search/edit-learning
      features continue normally, while metadata controls explain that local LLM
      generation is off.
- [ ] Update onboarding, health text, empty-model messages, translations, and docs
      to name the selected provider rather than claiming all configured providers
      are simultaneously active.

## Provider discovery and identity

- [ ] Register `axengine` dynamically in `AnalysisService`, using the same
      available/registered/failed lifecycle as Ollama and LM Studio, but query and
      expose only the active provider outside diagnostics.
- [ ] Implement a short-timeout `is_available()` using loopback `GET /health` and
      verify the returned service identity is `ax-engine-server`.
- [ ] Fetch `/api/version`, `/v1/runtime`, and `/v1/models` with bounded timeouts
      and response-size limits. Do not log complete unexpected response bodies.
- [ ] Return one model descriptor for every resident card satisfying all of:
      text output, chat-completions support, image input, and native multimodal
      input support. Fail closed when any required capability is false or absent.
- [ ] Preserve provider-reported model ID, AX Engine version, context and output
      limits, selected backend, support tier, model family, tensor format,
      dense/MoE status, active experts, quantized-binding evidence, runtime host,
      and native multimodal capability. Mark unavailable fields unknown.
- [ ] Produce concise labels that distinguish model family, parameter/architecture
      metadata when supplied, MLX format, quantization evidence, and AX Engine.
      Never derive a quant name solely from the model ID.
- [ ] Ensure the normal metadata selector and developer benchmark automatically
      show every resident vision-capable AX model and never show text-only,
      embedding-only, delegated text-only, or incompletely manifested models.
- [ ] Refresh discovery on each `/models` request so AX models added or removed
      after StyleAI starts appear without restarting the StyleAI backend.

## Filesystem model discovery

- [ ] Add a machine-local AX model-root setting rather than storing the path in a
      catalog database. Seed the development configuration with
      `/Volumes/Thunderbolt/Models`, keep it user-editable, and avoid placing the
      user's volume path in reports unless sensitive diagnostics are opted in.
- [ ] Canonicalize the root and scan with explicit depth, directory-count,
      elapsed-time, and metadata-size limits. Do not follow symlinks outside the
      canonical root, inspect tensor contents, or recursively enumerate arbitrary
      mounted volumes.
- [ ] Prefer AX-native `model-manifest.json` plus `config.json` packages. Record
      native manifest schema, model family, tensor format, architecture, vision
      configuration, quantization metadata, and aggregate artifact bytes without
      guessing unsupported fields from directory names.
- [ ] Distinguish **discovered**, **AX-loadable**, **resident**, and
      **StyleAI-vision-eligible** states. Filesystem inspection is advisory; AX
      Engine's load response and `/v1/models` capability card remain authoritative.
- [ ] Show unsupported candidates with a concise reason in diagnostics, but not
      in the normal vision-model list. Initially fail closed on non-native MLX or
      delegated GGUF packages unless the running AX version explicitly loads them
      and advertises native image input.
- [ ] Cache only non-sensitive inventory metadata and invalidate it on root
      availability or directory modification changes. Handle an unmounted
      Thunderbolt volume as an ordinary actionable unavailable state.

## Managed AX Engine lifecycle and residency

- [ ] Implement the supervisor in the Python backend, not in Lightroom Lua. Start
      it lazily only when an AX model is selected for metadata or benchmarking;
      model-list discovery alone must remain cheap and process-free.
- [ ] Use a serialized state machine (`stopped`, `attaching`, `starting`, `ready`,
      `draining`, `stopping`, `failed`) shared by model discovery and inference so
      concurrent requests cannot launch duplicate servers or race a restart.
- [ ] Launch with an argument vector (never a shell string), an explicit absolute
      model path, `--host 127.0.0.1`, `--port 31418`, and `--offline`. Use a
      sanitized environment and bounded log capture; never enable downloads.
- [ ] Persist an ownership marker containing PID, process start token/identity,
      executable identity, port, and a random launch token. Verify all fields
      before signalling or removing lifecycle markers; never use `pkill`, kill by
      port, or trust a stale PID alone.
- [ ] Before launch, probe the port. Attach only if health and service identity
      prove it is AX Engine; otherwise report a port conflict without terminating
      the occupying process.
- [ ] Wait for readiness with bounded polling and expose startup progress and
      stderr-tail diagnostics without leaking prompts, image data, or full model
      paths in normal logs.
- [ ] Enforce exactly one resident AX model. Before loading a different selected
      model, drain StyleAI's local-LLM admission lane and restart the owned AX
      process; do not use `load_mode=add` from StyleAI.
- [ ] Never automatically load all discovered candidates. Multi-model benchmark
      selections run successively, with deterministic restart-based reclamation
      between model configurations.
- [ ] Do not restart during an in-flight request and never silently substitute a
      different model. An external AX instance exposes only its resident model and
      must be changed or restarted outside StyleAI.
- [ ] Stop an owned child on StyleAI backend idle shutdown using a bounded graceful
      termination followed by a verified-child fallback. Leave external AX
      Engine instances untouched. Lightroom must not register a shutdown callback.
- [ ] If the backend or Lightroom crashes, recover conservatively: validate any
      prior ownership marker against the live process before reattaching or
      cleaning it, and never signal a process whose identity cannot be proven.
- [ ] Surface separate actions/status for **Restart managed AX Engine to reclaim
      memory** and **External AX Engine must be restarted outside StyleAI**.

## Metadata generation contract

- [ ] Reuse `LLMProviderBase` prompt construction, response schema, keyword
      normalization, placeholder filtering, and output-field normalization.
- [ ] Send non-streaming `POST /v1/chat/completions` with the selected resident
      model ID, system message, user text part, inline JPEG `image_url` data URI,
      temperature, and explicit `max_completion_tokens`.
- [ ] Request `response_format=json_schema` only with AX Engine's documented
      post-hoc-validation subset. Add a tested `json_object` fallback only for AX
      versions that reject the schema shape; never silently accept unstructured
      text as successful metadata.
- [ ] Parse only the documented OpenAI response envelope and JSON object content.
      Preserve authoritative usage when present and mark token counts unavailable
      rather than inventing zero for runtimes that omit them.
- [ ] Preserve end-to-end provider timing. Record AX-provided runtime or route
      metadata only from documented fields; do not synthesize vision-prefill,
      decode, TTFT, cache, or MTP timings from process-global metrics.
- [ ] Map actionable HTTP failures: invalid media/schema/context (400),
      unauthorized server configuration (401), saturation (429), model draining
      or unavailable (503), and post-hoc structured-output rejection (502).
- [ ] Keep the existing single process-wide local-LLM/accelerator admission lane
      and default concurrency of one, even though AX Engine can schedule multiple
      resident models.
- [ ] Preserve normal StyleAI retry and cancellation boundaries. Do not retry
      deterministic 400/401/502 failures; use bounded retry/backoff only for
      transient 429/503 responses.

## Runtime-managed MTP and benchmark evidence

- [ ] Treat AX Engine speculation as a model/server load-time runtime decision,
      not a draft-model selection. Never send LM Studio `draftModel` controls or
      expose AX models in StyleAI's full-draft dropdown.
- [ ] Introduce a provider-neutral `runtime_managed` requested mode for AX Engine
      benchmark rows. Do not label an AX request as baseline or verified MTP merely
      because its package name contains `MTP`.
- [ ] Capture documented per-request route metadata when AX returns it, including
      the resolved speculation profile and MTP policy/activity fields. Record
      effective MTP only when request-level evidence confirms it remained active
      for a vision request.
- [ ] When request-level MTP evidence is unavailable, retain a successful metadata
      response with effective mode/activity `unknown`; do not claim acceleration.
- [ ] Never infer per-request MTP acceptance from `/metrics` deltas while multiple
      requests or models may be active. Process-global Prometheus counters are
      operational evidence, not safe item attribution.
- [ ] Do not offer an in-process direct-versus-MTP toggle unless AX Engine adds a
      documented per-request control. Direct/MTP comparisons should use separately
      configured server runs and matching proxy/prompt/settings hashes.
- [ ] Extend benchmark CSV/JSONL identity with AX Engine version, backend/support
      tier, model family/tensor format, context/output limits, runtime speculation
      profile, request-level MTP verification, and fallback reason.

## Successive multi-model benchmarking

- [ ] Treat `/v1/models` as the complete single-resident inventory and always send
      the chosen card's exact `id` in every request.
- [ ] Verify two or more discovered vision models can be selected in one benchmark
      and are processed successively using identical proxies and settings, with no
      overlap in residency.
- [ ] Preserve deterministic benchmark ordering and record the resident inventory
      plus the exact selected order at run start; do not silently substitute the
      AX default model if a selected model is unloaded mid-run.
- [ ] If the selected model disappears or drains, fail that configuration with a
      classified error and continue to the next model. Never reroute to a sibling.
- [ ] Surface an actionable message when AX cannot load the next model after the
      prior owned process has stopped. Do not advise disabling its safety checks.
- [ ] Resolve every load target from the discovered catalog beneath the configured
      root; never accept an arbitrary model path from a metadata request. Pass the
      exact absolute artifact directory to AX and let AX validate its manifest.
- [ ] Add a Help > Plug-in Extras “Inspect AX Engine” diagnostic showing lifecycle
      ownership, root availability, discovered/resident/eligible inventories,
      memory preflight failures, and a redacted log location.

## Tests and validation

- [ ] Add provider tests for unavailable service, wrong service identity,
      non-loopback/redirect rejection, timeout, malformed JSON, authentication,
      and bounded error handling.
- [ ] Add active-provider tests for every provider and `Disabled`, filtered model
      discovery, stale model clearing/restoration, mismatched-provider rejection,
      no implicit fallback, switching while busy, and switching away from owned
      versus external AX Engine.
- [ ] Add supervisor tests for attach-versus-own, occupied non-AX port, duplicate
      launch suppression, readiness timeout, stale PID reuse, mismatched process
      token, restart while busy, external-process protection, graceful stop, and
      crash recovery.
- [ ] Add bounded scanner tests for unmounted roots, symlink escape, excessive
      depth/count/metadata size, malformed manifests/configs, native MLX vision,
      non-native MLX, GGUF, and model directories appearing/disappearing.
- [ ] Add discovery fixtures containing multiple resident models: two valid
      vision-chat models, text-only, embedding-only, delegated text-only, missing
      capability fields, and a model that disappears between discovery/inference.
- [ ] Add request-shape tests for inline JPEG data URIs, prompts, schema, sampling,
      model routing, max output tokens, and no photo bytes in logs.
- [ ] Add response tests for valid structured metadata, invalid output 502,
      authoritative/missing usage, MTP evidence present/absent, 429/503 retry
      classification, and provider timing.
- [ ] Add service/route/Lua tests proving `axengine` appears in provider health,
      model selectors, ordinary metadata generation, and benchmark reports while
      AX draft-model selection remains unavailable, and proving inactive-provider
      models never appear in either user-facing selector.
- [ ] Run focused provider/service/route tests, the full backend suite, Ruff,
      Lightroom plug-in validation, translation synchronization, and packaging.
- [ ] With explicit user approval, run one small real-image smoke against a live
      AX model that advertises image support, then a 12-photo benchmark before a
      normal 24–32-photo comparison.

## Live-validation prerequisites

- [ ] Stop the currently external AX Engine instance before testing managed launch,
      or deliberately keep it running to validate safe external attachment. Never
      have the test suite terminate it implicitly.
- [ ] Launch one discovered AX-native candidate and confirm its `/v1/models` card
      explicitly reports `capabilities.input.image=true` and
      `ax_engine.native_multimodal_input_supported=true` before sending a photo.
- [ ] For multi-model testing, select a second discovered image-capable model and
      confirm the first AX process exits before the second model begins loading.
- [ ] Verify an owned restart actually releases the prior model's unified-memory
      residency before loading the next large benchmark configuration.
- [ ] After each restart, re-query `/health` and `/v1/models` and confirm only the
      newly selected exact ID is resident and vision-capable before sending the
      next StyleAI live request.
