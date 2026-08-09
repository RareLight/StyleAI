# StyleAI Wiki

StyleAI is a local-first Lightroom Classic assistant that learns editing
decisions, applies high-confidence absolute Develop targets, builds a local
visual index, and optionally generates metadata with a local vision-language
model.

## User guides

- [Getting Started](Getting-Started)
- [Plugin Guide](Plugin-Guide)
- [Prepare Photos](Help-Analyze-and-Index)
- [Choosing a Local Metadata Model](Help-Choosing-AI-Model)
- [Ollama Setup](Help-Ollama-Setup)
- [LM Studio Setup](Help-LM-Studio-Setup)
- [Data, Privacy, and Security](Privacy-and-Security)
- [Troubleshooting](Troubleshooting)

## Developer references

- [Architecture and Data Pipelines](Architecture)
- [Developer Guide](Developer-Guide)
- [Background Service Guide](Background-Service-Guide)
- [Background Service Reference](Background-Service-README)
- [Credits and Dependencies](Credits)

The active Lightroom catalog owns one adjacent `styleai.db`. StyleAI backups
protect that AI data only; use Lightroom's own backup workflow for the catalog,
source photos, and Develop history.
