# Credits and Dependencies

StyleAI originated as a fork of LrGeniusAI by Bastian Machek and has been
extensively refactored and expanded by Anna Grunseth and open-source
contributors.

Core runtime projects include:

- Adobe Lightroom Classic SDK and Lua 5.1
- Flask and Waitress
- OpenCLIP, SigLIP2, PyTorch, Torchvision, timm, and Hugging Face tooling
- ChromaDB and SQLite
- NumPy and scikit-learn
- Pillow and psutil
- Ollama and LM Studio Python SDKs
- JSON.lua by Jeffrey Friedl

The locked, authoritative Python dependency versions are in
`server/pyproject.toml` and `server/uv.lock`. Model and library licenses remain
the responsibility of their respective projects and selected local models.

StyleAI itself is licensed under AGPL-3.0.
