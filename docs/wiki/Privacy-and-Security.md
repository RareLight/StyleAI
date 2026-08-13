# Data, Privacy, and Security

StyleAI processes catalog photos and metadata on the Lightroom computer.

- The backend listens only on `127.0.0.1:19819`.
- SigLIP2 indexing and learned editing run locally.
- Metadata generation connects only to local Ollama or LM Studio instances.
- No cloud AI providers, API keys, analytics, or usage telemetry are supported.
- Each catalog owns an adjacent `styleai.db`; catalogs must use separate
  folders, and backup database-marker mismatches fail closed.

Lightroom generally exports bounded JPEG proxies with embedded EXIF omitted.
The plug-in separately sends only workflow-relevant context to the loopback
service. Camera/lens/rendering evidence supports editing; GPS, existing
keywords, folder names, and manual context are included in metadata prompts
only when enabled.

Training/edit inference may read a RAW/DNG path to extract an embedded,
target-independent preview. Source files are never modified. Lightroom metadata
and Develop settings change only through visible plug-in actions.

StyleAI can access the internet for update checks, documentation, software
downloads, and initial model downloads, but those requests do not include
catalog images or photo metadata. Ollama and LM Studio are separate applications
whose own settings remain the user's responsibility.

Diagnostic image capture is off by default and requires an explicit capture
toggle. Captures stay in a local user-selected folder, can contain pixels and
metadata, and are bounded/clearable. Inspect diagnostic reports before sharing
them.

See the repository [privacy statement](https://github.com/RareLight/StyleAI/blob/main/PRIVACY.md)
for storage and retention details.
