# Repository audit remediation

Baseline: `d2fabc3`. The acceptance checks below use disposable catalogs and
local fixtures; none requires customer data, a provider, or a model download.

- [x] Catalog ownership: attach on every Lightroom readiness path, carry the
  catalog path on API calls, and stop before dispatch on a mismatch.
- [x] Initialization: serialize binding, migrations, recovery and markers;
  retry the entire unfinished initialization after failure.
- [x] Training: reuse only an embedding for the resolved RAW source in both
  upload paths. Account for previously mislabeled training records.
- [x] Policy publication: roll back shared example changes if a candidate
  cannot activate; a retirement cleanup failure must not delete active files.
- [x] Lightroom metadata: fetch every result before acquiring the write lane;
  fail a missing result without partially writing that batch.
- [x] Edit outbox: replay receipts for virtual copies and handle jobs that
  were interrupted or expired without losing the application event.
- [x] Maintenance tools: preserve the entire database and its backup chain on
  reset; never remove quarantine from unrelated downloads.
- [x] Restore: interrupt restored work even for the same process token and
  discard staging when the pre-restore backup fails.
- [x] Discard: verify removal of generated metadata against real disposable
  Chroma storage while retaining embeddings and other fields.
- [x] Prune: enumerate both collections, including training-only records;
  rebuild once rather than per deletion.
- [x] Jobs: return compact acknowledgments and register large selections in
  bounded chunks without exposing a partially registered job to workers.
- [x] Pressure: respect explicit GPU maxima and refuse impossible byte claims
  rather than waiting indefinitely.
- [x] Updates: use the full-release download flow for both manual and
  background checks. Do not offer an in-place patch lacking a working client,
  release manifest and frozen updater. Preserve the offline/no-update option.
- [x] Installation: explicitly distinguish the supported macOS/Windows ZIPs
  from the legacy packaging recipes. Do not direct users to an installer that
  references a launcher missing from the frozen distribution.
- [x] Closeout: isolated regressions, full backend tests, lint/format, Lua
  validation, translation consistency, synthetic policy checks and rerun guidance.

## Verification and release handoff

- `server/test/test_catalog_recovery_contracts.py`: injected migration failures,
  rejected foreign prune, same-process restore, staging cleanup, stored Chroma
  readback, training-only pruning, RAW vs rendered reuse, GPU ceilings, and
  100,001 registered items. Partial registrations are not admissible.
- `server/test/test_policy_runtime.py`: fail publication after replacing example
  data, then fail retirement cleanup after activation. Check the previous
  example targets in the first case and reload the active artifact in the second.
- Isolated fixture measurements (not a real-catalog accuracy claim): global and
  oracle target NRMSE 0.14636 and 0.00596. The source gate admitted 240/240
  training-fixture rows and abstained on the boundary probe. On three labelled
  fixture reviews the strict configuration selected 6/9, with membership
  precision 6/6 and cross-policy leakage 0/6.
- Reset was exercised only against a temporary directory with a stubbed port
  check: a catalog lock blocked reset; the intact backup chain survived reset.
  Mocked Lua update checks opened the release page without fetching a manifest.
- [ ] Before release: verify the catalog switch, batch metadata writes, replay
  after interruption on a virtual copy, and ZIP upgrade in Lightroom Classic
  with disposable catalogs on macOS and Windows. No live SDK or provider was
  used for this implementation.

## Existing catalogs

Run **Learn From My Edits** on the original RAW/DNG training selection. The
previous format cannot prove an old training vector really came from RAW
pixels. Preflight refreshes those examples even without forced retraining;
valid vectors from **Prepare Photos** remain reusable. **Prepare Photos is not
required.** Let training finish its rebuild. Use **Styles & Training → Rebuild**
only if training succeeded but rebuilding was interrupted or deferred;
rebuilding alone cannot repair an old vector.
