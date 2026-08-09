# Background Service Guide

The Python 3.12+ Flask/Waitress service performs image analysis, durable job
admission, local metadata inference, learned-policy training/editing,
recommendations, evaluation, and catalog-local persistence. It binds only to
`127.0.0.1:19819`.

## Startup and shutdown

The Lightroom plug-in starts one packaged backend or the current source backend
and initializes it with `<catalog folder>/styleai.db`. The process cannot switch
database paths, and backup marker checks prevent an archive from being restored
into another StyleAI database. Keep each Lightroom catalog in its own folder.

Lightroom exit does not send a shutdown request or wait on the service. The
service unloads idle SigLIP2 weights after 10 minutes and exits after 10 idle
minutes only when no job, resource lease, or index queue work is live. An
explicit source redeploy uses `scripts/server.sh stop` to cancel work and verify
port release before replacing the plug-in.

## Work and resource coordination

Indexing, metadata, training/discovery, recommendations, and editing use durable
operation jobs with per-photo state and scoped cancellation. Accelerator and
local-LLM lanes are process-wide. Multiple Lightroom tasks therefore share the
detected hardware budget instead of creating independent GPU or LLM pools.

Apple Silicon startup maxima scale with unified memory, and a runtime pressure
governor may reduce in-flight CPU/GPU/image-byte work. Local metadata inference
remains serialized by default because parallel model contexts normally reduce
throughput and increase swap risk.

## Database protection

The service automatically creates one validated snapshot per day and keeps the
newest 14 by default. It also requires a durable snapshot before pruning,
deleting all training data, schema migration, or restore.

In Plug-in Manager → **Data & Recovery**:

- **Export Backup...** writes a validated ZIP containing the complete StyleAI
  database, versioned manifest, database marker, sizes, and SHA-256 checksums.
- **Restore Backup...** validates a same-catalog archive, creates a pre-restore
  snapshot, drains live work, performs an atomic replacement, checks SQLite,
  and rolls back on failure.
- **Clean Up Removed Photos...** removes orphaned StyleAI records only after a
  pre-cleanup backup.

StyleAI backups do not contain the Lightroom catalog, source photos, or Develop
history. Use Lightroom's catalog backup separately.

## Diagnostics

Plug-in Manager → **Support & Debug** can generate a local support report from
service health and available logs. Debug image capture is off by default,
requires both Debug and capture to be enabled, may contain photo pixels and
metadata, and has bounded local retention.

See [Architecture](Architecture) and the [Developer Guide](Developer-Guide) for
the API, storage, and ML contracts.
