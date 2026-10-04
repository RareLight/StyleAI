# Applied Edit Quality UI Implementation Checklist

Status: backend, CLI, and developer Lightroom workflow implemented on August
27, 2026; Lightroom human gates remain. This plan
moves the existing applied-edit quality evaluator behind the catalog-local
backend and adds a developer-facing Lightroom workflow. It is the measurement
and reporting prerequisite for `STYLE_REFINEMENT_IMPLEMENTATION_CHECKLIST.md`;
preference collection, candidate construction, activation, and rollback belong
to that checklist. Reports here remain read-only and cannot mutate an active
editing policy or threshold.

Implemented scope includes the shared atomic/reusable report service, durable
`applied_edit_evaluation` job and HTTP polling, CLI parity, bounded Lightroom
summary, report reveal action, parameter validation, cancellation, maintenance
exclusion, focused tests, and developer Help-menu registration. Production
rating behavior was not changed. The unchecked Lightroom gates below remain
authoritative before this workflow can be considered complete.

## Product decision and boundaries

- [ ] Keep **Rate Selected AI Edits...** as the only production feedback action
      until **Refine Styles...** passes its developer, held-out, and Lightroom
      release gates. Rating records explicit outcomes and fresh Lightroom
      readback; it does not require running an analysis report.
- [ ] Add **Developer: Evaluate Applied Edit Quality...** under **Help > Plug-in
      Extras**, separate from the six normal File-menu photography workflows.
- [ ] Keep `server/scripts/evaluate_applied_edits.py` as a thin CLI/automation
      entry point over the same backend service used by Lightroom.
- [ ] Label all aggregate results **evaluation only**. Running a report must not
      rebuild styles, activate a generation, change confidence gates, adjust
      recommendation admission, or write Lightroom metadata.
- [ ] Keep all report inputs and outputs catalog-local. Do not add telemetry,
      cloud analysis, external service URLs, photo export, or network egress.
- [ ] Do not expose Brier scores, calibration bins, Wilson intervals, or raw
      correction tables as responsibilities for ordinary users.
- [ ] Keep candidate-building and production-facing refinement out of this
      report workflow. Implement them only through the separate Style
      Refinement checklist with held-out validation, explicit activation, and
      rollback.

## End-user rating use cases

Ratings are optional catalog-local evidence. Photographers do not need to rate
edits for StyleAI to apply styles, save training examples, rebuild learned
styles, or locate additional training candidates.

### Direct value available today

- [ ] Preserve **StyleAI Edit Review** metadata as a user-facing record of
      **Kept**, **Modified and Kept**, or **Not Useful**, separate from the
      historical Applied marker and current reconciliation state.
- [ ] Let photographers use that metadata to resume an interrupted review,
      avoid rating the same inference twice, and locate edits they intentionally
      kept, corrected, or rejected without reconstructing those decisions from
      Lightroom History.
- [ ] Treat **Modified and Kept** as a fresh modeled-slider readback. This gives
      the report concrete correction evidence while leaving the user's final
      Develop settings untouched.
- [ ] Keep corrected ratings append-only and retain the latest explicit outcome
      as the current review label.
- [ ] Keep all evidence local unless the photographer deliberately exports or
      shares a report. StyleAI has no telemetry channel through which the
      developers can silently peruse user ratings.

### Reasonable occasions to ask an end user for ratings

- [ ] During an opt-in beta or quality study, ask for a bounded, representative
      sample spanning different styles, profiles, lighting, subjects, and edit
      starting states.
- [ ] After activating a substantially new policy generation, ask willing
      testers for enough new reviews to compare it with prior generations.
- [ ] During support investigation of a repeatable quality problem, use ratings
      and correction readbacks to distinguish application failures, unsuitable
      policy choices, and usable edits that merely needed slider corrections.
- [ ] When a photographer wants a personal quality audit, let them review a
      representative sample and inspect the resulting local summary without
      implying that every applied edit must be rated.
- [ ] Never require ratings as a prerequisite for applying edits, retraining,
      backup/restore, or ordinary catalog use.

### Analytics value for development

- [ ] Use aggregate outcomes to measure acceptance, kept-after-modification,
      rejection, application reliability, review coverage, and confidence
      calibration.
- [ ] Use fresh correction readbacks to identify target families and sliders
      with systematic residual error. Keep rejected edits out of numeric
      regression targets.
- [ ] Compare policy generations only after minimum evidence requirements are
      met, and show uncertainty rather than ranking sparse generations.
- [ ] Use burst-coherence, rendering, policy, and partition breakdowns to find
      regressions or leakage that aggregate acceptance alone could hide.
- [ ] Require an explicit user action to export/share a report before project
      developers can inspect another user's evidence.

### Future direct-learning boundary

- [ ] Do not claim that a rating currently teaches the active model. Today it
      records evaluation evidence and user-facing review metadata only.
- [ ] Before ratings affect future edits, complete the inactive candidate,
      held-out evaluation, explanation, activation, and rollback gates in
      `STYLE_REFINEMENT_IMPLEMENTATION_CHECKLIST.md`.
- [ ] Never train numerically toward a rejected edit, because rejection records
      preference without supplying a valid target state.
- [ ] Never let aggregate ratings silently change confidence thresholds,
      recommendation admission, policy identity, or active artifacts.

## Shared evaluation/reporting service

- [ ] Extract report orchestration from `evaluate_applied_edits.py` into a
      backend service, while retaining
      `services.edit_quality_evaluation.evaluate_applied_edit_histories` as the
      versioned mathematical evaluator.
- [ ] Give the shared service one typed request contract containing:
      `created_after`, `confidence_bin_count`, and
      `minimum_reviewed_per_generation`.
- [ ] Keep defaults aligned with the current CLI: five confidence bins and at
      least 30 reviewed edits per generation for comparison.
- [ ] Validate date boundaries and numeric limits at the HTTP boundary. Keep
      supported bin counts within 2–20 and the minimum evidence count positive.
- [ ] Traverse inference/event history using the existing bounded keyset
      iterator. Do not introduce fixed catalog ceilings or unbounded SQL reads.
- [ ] Add cancellation checks between history batches and major evaluator
      phases. If profiling shows the evaluator's current materialized history
      list is material at large-catalog scale, refactor it to retain compact
      aggregate observations rather than complete histories.
- [ ] Generate the dataset fingerprint, schema version, UTC timestamp, platform
      summary, and full v2 report identically for CLI and UI runs.
- [ ] Write JSON atomically beneath
      `styleai.db/evaluation_reports/applied-edits-<fingerprint>.json`.
- [ ] Reuse an existing report when its schema, dataset fingerprint, and request
      parameters match; do not create duplicate files for identical evidence.
- [ ] Return a bounded UI summary separately from the full report. Include:
      application reliability, applied/reviewed counts, review coverage,
      accepted/modified/rejected counts and rates, evidence sufficiency by
      generation, confidence-calibration status, top corrected target families,
      and burst-coherence warnings.
- [ ] Preserve the versioned JSON schema as the detailed interchange contract.
      Add a new schema version only if the report shape changes.

## Durable backend workflow and HTTP API

- [ ] Add `applied_edit_evaluation` to the operation-kind registry.
- [ ] Add a job-backed endpoint such as `POST /style_edit/evaluation` that
      creates or attaches to an identical active evaluation request.
- [ ] Add polling through `GET /style_edit/evaluation?job_id=<id>` using the
      standard `results`/`error`/`warning` response envelope.
- [ ] Use the existing scoped operation-cancellation endpoint and durable job
      state instead of inventing a separate cancellation mechanism.
- [ ] Run evaluation on a daemon worker, mirroring the established upgrade-
      recommendation job pattern, so a large catalog cannot hold an HTTP
      request or Lightroom modal open indefinitely.
- [ ] Acquire a bounded `cpu_prepare` resource and the workflow side of the
      maintenance gate. Evaluation must not claim the accelerator or local-LLM
      lanes and must not overlap a destructive restore.
- [ ] Store only bounded summary data and the report path in operation details;
      do not put the complete report into the jobs table or HTTP response.
- [ ] Mark canceled, interrupted, failed, and succeeded jobs explicitly. A
      failed or canceled run must not leave a temporary report presented as
      complete.
- [ ] Recover or invalidate an interrupted evaluation job on service startup
      using the existing operation recovery rules.
- [ ] Log report identity, aggregate counts, duration, and failures without
      logging photo IDs, Develop settings, absolute source paths, or event
      payloads.

## CLI compatibility

- [ ] Refactor `server/scripts/evaluate_applied_edits.py` to call the shared
      reporting service directly rather than duplicate orchestration.
- [ ] Preserve all existing CLI arguments and exit behavior.
- [ ] Preserve explicit `--output` support for automated analysis, while the UI
      always uses the catalog-local report directory.
- [ ] Make CLI and HTTP runs over identical evidence produce the same dataset
      fingerprint, metrics, and schema-valid report content, apart from allowed
      runtime metadata such as generation time.
- [ ] Keep CLI availability documented for CI, batch experiments, and agents;
      state clearly that photographers do not need to run it.

## Lightroom developer workflow

- [ ] Register **Developer: Evaluate Applied Edit Quality...** literally in
      `Info.lua` under **Help > Plug-in Extras**.
- [ ] Implement the entry point with `LrTasks.startAsyncTask` and
      `LrTasks.pcall`; do not add a shutdown callback.
- [ ] Snapshot no photo selection because the canonical report evaluates
      catalog-local history, not an arbitrary selected subset.
- [ ] Present a compact setup dialog with:
      **All recorded history** or **Since last successful report**,
      the default minimum of 30 reviews per generation, and a clear
      evaluation-only explanation.
- [ ] Keep confidence-bin count in an Advanced section or fixed at the tested
      default unless changing it has a demonstrated developer use case.
- [ ] Start or attach to the durable backend job, show cancellable progress, and
      poll at a bounded interval without blocking Lightroom's UI thread.
- [ ] On completion, show a readable summary organized as:
      Evidence, Outcomes, Reliability, Corrections, Generation comparison, and
      Warnings/insufficient evidence.
- [ ] Translate statistical language into decisions without overstating it.
      For example, show “Needs 12 more reviewed edits for comparison” rather
      than only `insufficient_reviews`.
- [ ] Distinguish **accepted unchanged** from **kept after modification**; do
      not merge those into a single acceptance metric.
- [ ] Include buttons to **Reveal Full JSON Report** and **Close**. Add **Copy
      Summary** only if the Lightroom SDK implementation is reliable on all
      supported platforms.
- [ ] Never show a “Promote,” “Apply calibration,” “Retrain,” or “Fix model”
      button in this workflow.
- [ ] If there are no applied edits or no explicit reviews, return a successful
      empty-evidence explanation with the next useful action, not an error.

## Optional production-facing progress, deliberately limited

- [ ] Evaluate after developer testing whether **Styles & Training** benefits
      from one nontechnical line such as “48 of 73 applied edits reviewed.”
- [ ] If added before Style Refinement ships, make that line informational and
      link only to **Rate Selected AI Edits...**. After refinement ships, the
      product checklist may replace it with a simple refinement-readiness or
      resumable-session action; never expose developer statistics as a user
      responsibility.
- [ ] Do not block editing, training, recommendations, or normal use based on
      review coverage.

## Automated tests

- [ ] Unit-test shared orchestration, atomic output, deterministic fingerprints,
      parameter validation, empty evidence, insufficient evidence, and report
      reuse.
- [ ] Prove CLI and backend entry points produce equivalent reports from one
      deterministic fixture.
- [ ] Add route tests for create/attach, poll, success, failure, cancellation,
      malformed parameters, missing catalog database, and standard envelopes.
- [ ] Add operation recovery tests for interrupted evaluation jobs.
- [ ] Add concurrency tests proving evaluation waits for maintenance/restore
      exclusion but does not claim accelerator or LLM capacity.
- [ ] Add privacy tests proving logs and responses omit photo IDs, source paths,
      observed Develop values, and complete event histories.
- [ ] Add large synthetic-history tests that cross multiple keyset pages and
      verify bounded query sizes, deterministic output, and responsive
      cancellation.
- [ ] Add plug-in packaging/static tests for the developer Help-menu entry,
      asynchronous task boundary, polling/cancellation, visible strings, and
      absence from the File-menu production workflows.
- [ ] Validate the v2 JSON output against
      `docs/schemas/applied-edit-quality-v2.schema.json`.

## Documentation and localization

- [ ] Update `docs/UI_BEHAVIOR_CONTRACTS.md` with the developer-only workflow,
      report location, no-mutation guarantee, and empty-evidence behavior.
- [ ] Update the Developer Guide with both UI and CLI paths and their shared
      implementation.
- [ ] Update the Plugin Guide to state that users only rate edits; StyleAI does
      not require them to run or interpret aggregate evaluation reports.
- [ ] Synchronize English, Catalan, German, Spanish, and French strings.

## Validation and Lightroom gates

- [ ] Run focused service, route, operation, report-schema, and packaging tests.
- [ ] Run `bash server/scripts/lint_format.sh`.
- [ ] Run `(cd server && uv run pytest test/)`.
- [ ] Run `python scripts/validate_lrc_plugin.py`.
- [ ] Run `python sync_translations.py` and confirm a second run is clean.
- [ ] In Lightroom, run the tool with no evidence and verify the guidance is
      clear and non-erroring.
- [ ] Run it with fewer than 30 reviewed edits in one generation and verify the
      exact remaining evidence requirement is shown.
- [ ] Run it with at least 30 reviewed edits and verify counts/rates against the
      CLI report from the same frozen database.
- [ ] Cancel during history traversal and verify Lightroom stays responsive,
      the operation becomes terminal, and no complete report is advertised.
- [ ] Start a restore while evaluation is active and verify maintenance
      exclusion prevents database replacement races.
- [ ] Quit Lightroom during evaluation and verify shutdown remains prompt; the
      backend job must finish, cancel, or recover without a plug-in shutdown
      hook.
- [ ] Reveal the report and validate its JSON against the checked-in schema.

## Completion criteria

- [ ] A developer can obtain the same applied-edit quality assessment from
      Lightroom or the CLI without manually locating the catalog database.
- [ ] Style Refinement can consume the same versioned metrics and report
      fingerprints for candidate gates without duplicating or weakening the
      evaluator.
- [ ] An ordinary user can collect feedback entirely through the production
      Lightroom workflow and is never required to run an external script.
- [ ] Evaluation remains local, read-only with respect to model state, bounded,
      cancellable, versioned, reproducible, and explicitly non-promotional.
