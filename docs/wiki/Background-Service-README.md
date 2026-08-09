# Background Service Reference

The service is the local compute and persistence layer behind the Lightroom Lua
plug-in.

## Runtime stack

- Python 3.12+, Flask, and Waitress
- OpenCLIP/PyTorch SigLIP2 vision inference
- ChromaDB for isolated visual-index and edit-training embeddings
- SQLite plus versioned joblib artifacts for transactional policy state
- scikit-learn/NumPy for conditional policy fitting and evaluation
- Ollama and LM Studio adapters for optional local metadata generation

## HTTP surface

- `/initialize`, `/health`, `/version`, `/models`: catalog binding and status
- `/operations*`: durable jobs, per-photo state, completion, and cancellation
- `/index*`, `/metadata/generate_batch`: visual analysis and metadata
- `/training*`: training-example mutation and inspection
- `/styles*`: asynchronous discovery, catalog, names, and recommendations
- `/style_edit*`: inference plus append-only application/reconciliation/outcome events
- `/db/stats`, `/db/backup`, `/db/restore`, `/db/prune`: guarded maintenance
- `/shutdown`, `/unload`, `/logs`, `/debug/captures`: lifecycle/support

Responses use the standard `results`, `error`, and `warning` envelope. Business
logic belongs in `services`, not route handlers.

## Data guarantees

- One process binds to one `styleai.db` path; backup restores require its
  generated database marker, and each Lightroom catalog must use its own folder.
- `image_embeddings` and `edit_training` remain strictly isolated.
- Policy generations activate atomically only after every row and artifact is
  valid; failed builds leave the prior generation active.
- Backend completion remains nonterminal while a Lightroom catalog or Develop
  handoff is pending.
- Backups, restore, prune, resets, migration, and activation drain conflicting
  live workflows through maintenance admission.
- Edit inferences are immutable and events are append-only.

For operational use, see [Background Service Guide](Background-Service-Guide).
