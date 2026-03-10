# paperless-lightrag-sync

Sync service that continuously transfers documents from [Paperless-ngx](https://docs.paperless-ngx.com/) to [LightRAG](https://github.com/HKUDS/LightRAG) for cross-document knowledge graph querying.

## Features

- Incremental sync — only processes new/modified documents
- Daemon mode with configurable sync interval
- Graceful shutdown (SIGTERM/SIGINT)
- Health check via state file age
- Enriched document text with metadata (title, correspondent, document type, tags, dates)
- Document links as `file_source` for clickable references in LightRAG
- No external Python dependencies (stdlib only)
- Multi-arch Docker image (amd64 + arm64)

## Docker Usage

```yaml
services:
  paperless-lightrag-sync:
    image: ghcr.io/hackwell/paperless-lightrag-sync:main
    restart: unless-stopped
    environment:
      PAPERLESS_URL: "http://webserver:8000"
      PAPERLESS_TOKEN: "your-paperless-api-token"
      LIGHTRAG_URL: "http://lightrag:9621"
      PAPERLESS_BASE_URL: "https://your-paperless-domain.com"
      SYNC_INTERVAL: "1800"
    volumes:
      - ./sync-data:/app/data
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PAPERLESS_URL` | `http://webserver:8000` | Internal Paperless-ngx URL |
| `PAPERLESS_TOKEN` | (required) | Paperless API authentication token |
| `LIGHTRAG_URL` | `http://lightrag:9621` | Internal LightRAG URL |
| `PAPERLESS_BASE_URL` | `https://dms.weller.cc` | Public Paperless URL for document links |
| `STATE_FILE` | `/app/data/sync_state.json` | Path to sync state persistence file |
| `SYNC_INTERVAL` | `1800` | Seconds between sync runs (default: 30 min) |

## Manual Sync

```bash
# One-time incremental sync
docker exec paperless-lightrag-sync python3 sync_paperless_lightrag.py

# Full re-sync (all documents)
docker exec paperless-lightrag-sync python3 sync_paperless_lightrag.py --full
```

## How It Works

1. Fetches all tags, correspondents, and document types from Paperless API
2. Iterates through documents (incremental: only modified since last sync)
3. Builds enriched text: title, Paperless link, correspondent, type, date, tags, content
4. POSTs to LightRAG `/documents/text` with `file_source` set to the public Paperless URL
5. Tracks synced document IDs in state file to avoid duplicates
6. Documents shorter than 50 characters are skipped

## Architecture

```
┌──────────────┐     API      ┌──────────┐     API      ┌──────────┐
│ Paperless-ngx│ ──────────── │  Sync    │ ──────────── │ LightRAG │
│              │  GET /api/*  │ Service  │ POST /docs   │          │
└──────────────┘              └──────────┘              └──────────┘
                                   │
                              ┌────┴────┐
                              │  State  │
                              │  File   │
                              └─────────┘
```
