# Ollama Setup

Ollama runs open-weights models locally and is used by StyleAI only for optional
metadata generation.

1. Install Ollama from [ollama.com](https://ollama.com/) and start its local
   service.
2. Use Ollama's current model catalog to choose and pull a vision-capable model
   that fits your hardware.
3. Verify it appears in `ollama list`.
4. Open Lightroom Plug-in Manager → StyleAI → **Configure Local Models...** and
   confirm the metadata provider is ready.
5. Choose the model under **Prepare Photos → Metadata Settings**.

StyleAI connects only to Ollama's loopback endpoint on port 11434. It does not
support a remote Ollama host. Keep Ollama running during metadata batches.
Learn From My Edits and Apply My Style do not require Ollama.
