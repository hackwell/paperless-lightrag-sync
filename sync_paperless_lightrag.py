#!/usr/bin/env python3
"""
Sync documents from Paperless-ngx to LightRAG.
Fetches documents via Paperless API and inserts them into LightRAG for
cross-document knowledge graph querying.

Configuration via environment variables:
  PAPERLESS_URL      - Paperless-ngx base URL (default: http://webserver:8000)
  PAPERLESS_TOKEN    - Paperless API token
  LIGHTRAG_URL       - LightRAG base URL (default: http://lightrag:9621)
  PAPERLESS_BASE_URL - Public Paperless URL for document links (default: https://dms.weller.cc)
  STATE_FILE         - Path to sync state file (default: /app/data/sync_state.json)
  SYNC_INTERVAL      - Seconds between sync runs in daemon mode (default: 1800)
"""

import json
import os
import sys
import time
import signal
import argparse
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path

PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://webserver:8000")
PAPERLESS_TOKEN = os.environ.get("PAPERLESS_TOKEN", "")
LIGHTRAG_URL = os.environ.get("LIGHTRAG_URL", "http://lightrag:9621")
PAPERLESS_BASE_URL = os.environ.get("PAPERLESS_BASE_URL", "https://dms.weller.cc")
STATE_FILE = Path(os.environ.get("STATE_FILE", "/app/data/sync_state.json"))
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "1800"))

# Graceful shutdown
shutdown_requested = False


def handle_signal(signum, frame):
    global shutdown_requested
    print("[" + datetime.now().isoformat() + "] Shutdown signal received, finishing current operation...")
    shutdown_requested = True


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def paperless_get(endpoint, params=None):
    """GET request to Paperless-ngx API."""
    url = PAPERLESS_URL + "/api/" + endpoint + "/"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": "Token " + PAPERLESS_TOKEN,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def lightrag_insert(text, file_source):
    """Insert a document text into LightRAG."""
    url = LIGHTRAG_URL + "/documents/text"
    payload = json.dumps({"text": text, "file_source": file_source}).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def load_state():
    """Load sync state from file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_sync": None, "synced_ids": []}


def save_state(state):
    """Save sync state to file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_tags_map():
    """Fetch all tags from Paperless and return id->name mapping."""
    tags = {}
    page = 1
    while True:
        data = paperless_get("tags", {"page": page, "page_size": 100})
        for tag in data.get("results", []):
            tags[tag["id"]] = tag["name"]
        if not data.get("next"):
            break
        page += 1
    return tags


def get_correspondents_map():
    """Fetch all correspondents from Paperless and return id->name mapping."""
    correspondents = {}
    page = 1
    while True:
        data = paperless_get("correspondents", {"page": page, "page_size": 100})
        for c in data.get("results", []):
            correspondents[c["id"]] = c["name"]
        if not data.get("next"):
            break
        page += 1
    return correspondents


def get_document_types_map():
    """Fetch all document types from Paperless and return id->name mapping."""
    doc_types = {}
    page = 1
    while True:
        data = paperless_get("document_types", {"page": page, "page_size": 100})
        for dt in data.get("results", []):
            doc_types[dt["id"]] = dt["name"]
        if not data.get("next"):
            break
        page += 1
    return doc_types


def build_document_text(doc, tags_map, correspondents_map, doc_types_map):
    """Build enriched text representation of a Paperless document."""
    parts = []

    title = doc.get("title", "Unbekannt")
    parts.append("Dokumenttitel: " + title)
    parts.append("Paperless-Link: " + PAPERLESS_BASE_URL + "/documents/" + str(doc.get("id", "")) + "/details")

    if doc.get("correspondent"):
        corr_name = correspondents_map.get(doc["correspondent"], "Unbekannt")
        parts.append("Absender/Korrespondent: " + corr_name)

    if doc.get("document_type"):
        dt_name = doc_types_map.get(doc["document_type"], "Unbekannt")
        parts.append("Dokumenttyp: " + dt_name)

    if doc.get("created"):
        parts.append("Erstellt: " + doc["created"][:10])

    tag_names = [tags_map.get(tid, "Tag-" + str(tid)) for tid in doc.get("tags", [])]
    if tag_names:
        parts.append("Tags: " + ", ".join(tag_names))

    content = doc.get("content", "").strip()
    if content:
        parts.append("\nInhalt:\n" + content)

    return "\n".join(parts)


def sync_documents(full=False):
    """Sync documents from Paperless to LightRAG."""
    state = load_state()
    last_sync = state.get("last_sync")
    synced_ids = set(state.get("synced_ids", []))

    mode = "full" if full else "incremental"
    now = datetime.now().isoformat()
    print("[" + now + "] Starting " + mode + " sync...")
    if not full and last_sync:
        print("  Last sync: " + last_sync)
        print("  Previously synced: " + str(len(synced_ids)) + " documents")

    # Fetch metadata maps
    print("  Fetching tags, correspondents, document types...")
    tags_map = get_tags_map()
    correspondents_map = get_correspondents_map()
    doc_types_map = get_document_types_map()
    print("  Found " + str(len(tags_map)) + " tags, " +
          str(len(correspondents_map)) + " correspondents, " +
          str(len(doc_types_map)) + " types")

    # Fetch documents
    params = {"page_size": 100, "ordering": "-created"}
    if not full and last_sync:
        params["modified__gt"] = last_sync

    total_synced = 0
    total_skipped = 0
    total_errors = 0
    page = 1

    while True:
        if shutdown_requested:
            print("  Shutdown requested, saving state...")
            break

        params["page"] = page
        print("  Fetching page " + str(page) + "...")
        data = paperless_get("documents", params)
        results = data.get("results", [])

        if not results:
            break

        print("  Processing " + str(len(results)) + " documents from page " + str(page) + "...")

        for doc in results:
            if shutdown_requested:
                break

            doc_id = doc["id"]
            file_source = PAPERLESS_BASE_URL + "/documents/" + str(doc_id) + "/details"

            if not full and doc_id in synced_ids:
                total_skipped += 1
                continue

            text = build_document_text(doc, tags_map, correspondents_map, doc_types_map)

            if len(text.strip()) < 50:
                print("    Skipping doc " + str(doc_id) + " (" +
                      doc.get("title", "?") + "): too short")
                total_skipped += 1
                continue

            try:
                result = lightrag_insert(text, file_source)
                status = result.get("status", "unknown")

                if status == "success":
                    total_synced += 1
                    print("    [" + str(total_synced) + "] Inserted doc " +
                          str(doc_id) + ": " + doc.get("title", "?")[:60])
                elif status == "duplicated":
                    print("    Duplicate doc " + str(doc_id) + ": " +
                          doc.get("title", "?")[:60])
                    total_skipped += 1
                else:
                    print("    Doc " + str(doc_id) + " status: " + status)
                    total_synced += 1

                synced_ids.add(doc_id)
                time.sleep(1)

            except urllib.error.HTTPError as e:
                error_body = e.read().decode() if e.fp else ""
                print("    ERROR doc " + str(doc_id) + ": HTTP " +
                      str(e.code) + " - " + error_body[:200])
                total_errors += 1
            except Exception as e:
                print("    ERROR doc " + str(doc_id) + ": " + str(e))
                total_errors += 1

        if not data.get("next"):
            break
        page += 1

    # Save state
    state["last_sync"] = datetime.now().isoformat()
    state["synced_ids"] = list(synced_ids)
    save_state(state)

    print("\n  Sync complete: " + str(total_synced) + " inserted, " +
          str(total_skipped) + " skipped, " + str(total_errors) + " errors")
    print("  Total tracked: " + str(len(synced_ids)) + " documents")

    return total_errors == 0


def wait_for_services():
    """Wait until both Paperless and LightRAG are reachable."""
    print("[" + datetime.now().isoformat() + "] Waiting for services...")
    for attempt in range(60):
        if shutdown_requested:
            return False
        try:
            # Check Paperless (use tags endpoint — root /api/ returns 406)
            req = urllib.request.Request(
                PAPERLESS_URL + "/api/tags/?page_size=1",
                headers={"Authorization": "Token " + PAPERLESS_TOKEN, "Accept": "application/json"})
            urllib.request.urlopen(req, timeout=5)

            # Check LightRAG
            req = urllib.request.Request(LIGHTRAG_URL + "/health")
            urllib.request.urlopen(req, timeout=5)

            print("  Both services reachable.")
            return True
        except Exception as e:
            if attempt % 10 == 0:
                print("  Attempt " + str(attempt + 1) + "/60: " + str(e))
            time.sleep(5)

    print("  ERROR: Services not reachable after 5 minutes.")
    return False


def run_daemon():
    """Run sync in a loop with configurable interval."""
    print("[" + datetime.now().isoformat() + "] Starting sync daemon (interval: " +
          str(SYNC_INTERVAL) + "s)")

    if not wait_for_services():
        sys.exit(1)

    # Initial sync
    try:
        sync_documents(full=False)
    except Exception as e:
        print("[" + datetime.now().isoformat() + "] Initial sync failed: " + str(e))

    # Loop
    while not shutdown_requested:
        # Sleep in small increments so we can respond to signals
        for _ in range(SYNC_INTERVAL):
            if shutdown_requested:
                break
            time.sleep(1)

        if shutdown_requested:
            break

        try:
            sync_documents(full=False)
        except Exception as e:
            print("[" + datetime.now().isoformat() + "] Sync error: " + str(e))

    print("[" + datetime.now().isoformat() + "] Daemon stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Sync Paperless-ngx documents to LightRAG")
    parser.add_argument("--full", action="store_true",
                        help="Full sync (ignore last sync timestamp)")
    parser.add_argument("--daemon", action="store_true",
                        help="Run as daemon with periodic sync")
    parser.add_argument("--paperless-url", default=None,
                        help="Override Paperless URL")
    parser.add_argument("--lightrag-url", default=None,
                        help="Override LightRAG URL")
    args = parser.parse_args()

    global PAPERLESS_URL, LIGHTRAG_URL
    if args.paperless_url:
        PAPERLESS_URL = args.paperless_url
    if args.lightrag_url:
        LIGHTRAG_URL = args.lightrag_url

    if not PAPERLESS_TOKEN:
        print("ERROR: PAPERLESS_TOKEN environment variable is required")
        sys.exit(1)

    if args.daemon:
        run_daemon()
    else:
        if not wait_for_services():
            sys.exit(1)
        success = sync_documents(full=args.full)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
