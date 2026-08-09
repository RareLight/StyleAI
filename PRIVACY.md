# StyleAI Privacy and Data Handling

**Last updated:** August 8, 2026

StyleAI is local-first. Photos, previews, catalog metadata, embeddings, learned
editing policies, recommendations, and edit history are processed and stored on
the same computer as Lightroom Classic.

## Local processing boundary

- The StyleAI service listens only on `127.0.0.1:19819`.
- Visual analysis and learned editing use local SigLIP2/PyTorch inference.
- Optional metadata generation connects only to Ollama or LM Studio running on
  loopback. StyleAI has no cloud AI provider or API-key support.
- StyleAI contains no analytics, usage telemetry, or advertising trackers.

StyleAI can access the internet for software update checks, user-initiated
downloads, external documentation, and initial model downloads. Those requests
do not include catalog photos or photo metadata. Ollama and LM Studio are
separate applications with their own configuration and policies.

## Stored data

Each Lightroom catalog owns one `styleai.db` beside its `.lrcat` file. Because
the database name is directory-local, keep each Lightroom catalog in a separate
folder.

| Data | Location | Retention |
| --- | --- | --- |
| Visual and training embeddings | Catalog-local ChromaDB | Until removed or the database is deleted |
| Training examples, policies, recommendations, jobs, and edit history | Catalog-local SQLite/artifact files | Until removed or the database is deleted |
| Temporary previews | Local temporary/cache storage | Bounded to the operation/cache lifecycle |
| Plug-in and service logs | Local log/catalog folders | Bounded rotation where supported |
| Diagnostic image captures | User-selected local folder | Only when Debug and capture are both enabled; bounded retention |

Validated StyleAI backups contain the catalog-local StyleAI database and its
manifest. They do not contain the Lightroom catalog, original photos, or
Lightroom Develop history. Backup archives are never uploaded automatically.

## Image and metadata handling

Lightroom usually exports bounded JPEG proxies for analysis. Exported proxies
omit embedded EXIF where possible. The plug-in may separately send selected
EXIF/IPTC context—such as camera/lens data needed for editing, or GPS, existing
keywords, and folder context explicitly enabled for metadata generation—to the
loopback service. This data remains local.

Training and edit inference may inspect an original RAW/DNG path to extract a
target-independent embedded preview. StyleAI does not modify the source file.
Lightroom catalog metadata and Develop settings are changed only through
visible plug-in workflows.

## Debugging and user control

Diagnostic reports remain local for inspection before the user shares them.
Debug image capture is off by default and requires two explicit controls. Such
captures can contain photo pixels and metadata; enable them only while
troubleshooting and clear them afterward.

Users can reveal, back up, restore, clean, or delete StyleAI data through the
Plug-in Manager and Styles & Training workflows. The live process cannot switch
database paths, and backup database-marker checks prevent an archive from being
silently restored into another StyleAI database.

Questions and issues: [github.com/RareLight/StyleAI/issues](https://github.com/RareLight/StyleAI/issues).
