FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Hackwell/paperless-lightrag-sync"
LABEL org.opencontainers.image.description="Sync service for Paperless-ngx to LightRAG"

WORKDIR /app

COPY sync_paperless_lightrag.py .

# No external dependencies needed — stdlib only

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python3 -c "from pathlib import Path; import json, sys, time; \
    s=json.loads(Path('/app/data/sync_state.json').read_text()); \
    from datetime import datetime; \
    last=datetime.fromisoformat(s['last_sync']); \
    age=(datetime.now()-last).total_seconds(); \
    sys.exit(0 if age < 7200 else 1)" \
  || exit 1

ENTRYPOINT ["python3", "-u", "sync_paperless_lightrag.py", "--daemon"]
