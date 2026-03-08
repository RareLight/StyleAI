# Server Guide

## Main documentation

The server documentation is maintained in:

- `server/README.md`

## Key responsibilities

- image indexing
- metadata persistence
- semantic search
- faces/person APIs
- database migration endpoints

## DB backup workflow

The backend now exposes a dedicated backup download flow:

- API endpoint: `GET /db/backup`
- output: a ZIP archive of the complete persistent backend DB directory
- includes: Chroma data plus accompanying SQLite and JSON files under the configured DB path

In Lightroom, open `File -> Plug-in Manager -> LrGeniusAI -> Backend Server` and click `Download DB backup`.

Use this backup before:

- one-time DB migrations
- server moves or rebuilds
- larger maintenance changes on the backend host
