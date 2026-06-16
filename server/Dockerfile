# styleai-server – für lokalen oder Remote-Betrieb als Container
# Build: docker build -t styleai-server .
# Run:   docker run -p 19819:19819 -v /pfad/zu/daten:/data -e STYLEAI_HOST=0.0.0.0 styleai-server
#
# API surface is everything under COPY src/ (index, search, clip, faces, import, db, server, …).
# AI Lightroom edit recipes: POST /edit (multipart) and POST /edit_base64 (JSON) – same image,
# no extra Dockerfile COPY beyond src/.

FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

WORKDIR /app

# Build tools for compiling dependencies like insightface
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeiten

COPY uv.lock pyproject.toml .
RUN uv sync --locked --no-dev

# App source
COPY src /app/src

# Remote-Zugriff: Server auf allen Interfaces binden
ENV STYLEAI_HOST=0.0.0.0
ENV STYLEAI_PORT=19819

# Model caches (Hugging Face + InsightFace) – keep persistent via mounted volume
ENV HF_HOME=/models/huggingface
ENV INSIGHTFACE_ROOT=/models/insightface

# Persistente Backend-Daten (Chroma/JSON/SQLite) per Volume mounten.
# Diese Dateien werden auch vom /db/backup-Endpunkt gesichert.
VOLUME /data
VOLUME /models

EXPOSE 19819

# DB-Pfad zeigt auf das persistente Backend-Datenverzeichnis unter /data/db.
CMD ["uv", "run", "/app/src/styleai_server.py", "--db-path", "/data/db"]
