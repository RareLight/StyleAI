# LM Studio Setup

LM Studio runs open-weights models locally and is used by StyleAI only for
optional metadata generation.

1. Install LM Studio from [lmstudio.ai](https://lmstudio.ai/) and download a
   vision-capable instruction model that fits your hardware.
2. Load the model and start LM Studio's local server. Port 1234 is the initial
   default; StyleAI also uses LM Studio's SDK to discover a dynamic loopback
   API port.
3. Open Lightroom Plug-in Manager → StyleAI → **Configure Local Models...** and
   confirm the metadata provider is ready.
4. Choose the model under **Prepare Photos → Metadata Settings**.

Remote and LAN-hosted LM Studio instances are rejected. On Apple Silicon,
benchmark an MLX build against other available formats, and leave enough
unified-memory headroom for Lightroom, SigLIP2, and the catalog database. Learn
From My Edits and Apply My Style do not require LM Studio.
