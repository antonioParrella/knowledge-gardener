"""
watchdog.py — Main entry point. Monitors the Obsidian vault for new notes
and routes them to the appropriate processing pipeline.

Run this script on your Surface. It will watch the vault continuously.
Add to Windows Task Scheduler or Startup folder to run automatically.

Usage:
    python watchdog.py

Routes:
    Inbox/          → clipper.process_clipped_note()
    _triggers/      → researcher.process_research_trigger()
"""

import sys
import time
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Add src to path so relative imports work when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    VAULT_PATH, INBOX_PATH, TRIGGERS_PATH, ALL_PATHS,
    ICLOUD_SETTLE_SECS, WATCH_RECURSIVE, GEMINI_API_KEY
)
from clipper import process_clipped_note, find_unprocessed_clips
from researcher import process_research_trigger

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(VAULT_PATH.parent / "watchdog.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── Startup checks ────────────────────────────────────────────────────────────

def startup_checks():
    """Validate config and create vault folders before starting."""
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY environment variable is not set. Exiting.")
        sys.exit(1)

    if not VAULT_PATH.exists():
        log.error(f"Vault path does not exist: {VAULT_PATH}")
        log.error("Update VAULT_PATH in config.py and try again.")
        sys.exit(1)

    for path in ALL_PATHS:
        path.mkdir(parents=True, exist_ok=True)
        log.info(f"Ready: {path.relative_to(VAULT_PATH)}/")

    # Process any unprocessed clips from previous sessions
    unprocessed = find_unprocessed_clips(INBOX_PATH)
    if unprocessed:
        log.info(f"Found {len(unprocessed)} unprocessed clip(s) from previous sessions")
        for path in unprocessed:
            try:
                log.info(f"Processing backlog: {path.name}")
                process_clipped_note(path)
            except Exception as e:
                log.error(f"Error processing backlog {path.name}: {e}")


# ── File event handler ────────────────────────────────────────────────────────

class VaultHandler(FileSystemEventHandler):
    """
    Watches the vault for new markdown files and routes them.
    Uses a small set to avoid double-processing the same file.
    """

    def __init__(self):
        self._processing = set()

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".md":
            return
        if path in self._processing:
            return

        self._processing.add(path)
        try:
            # Wait for iCloud to finish writing the file
            time.sleep(ICLOUD_SETTLE_SECS)

            # Re-check file still exists after settle (iCloud sometimes creates
            # temp files that disappear)
            if not path.exists():
                return

            self._route(path)

        except Exception as e:
            log.error(f"Error processing {path.name}: {e}", exc_info=True)
        finally:
            self._processing.discard(path)

    def _route(self, path: Path):
        """Route a new note to the appropriate pipeline."""
        try:
            parents = set(path.parents)

            if INBOX_PATH in parents:
                log.info(f"New clip detected: {path.name}")
                process_clipped_note(path)

            elif TRIGGERS_PATH in parents:
                log.info(f"New research trigger: {path.name}")
                process_research_trigger(path)

        except Exception as e:
            log.error(f"Pipeline error for {path.name}: {e}", exc_info=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    startup_checks()

    log.info("=" * 60)
    log.info("Obsidian Auto-Research System")
    log.info(f"Vault: {VAULT_PATH}")
    log.info("Watching for:")
    log.info(f"  Clips    → {INBOX_PATH.relative_to(VAULT_PATH)}/")
    log.info(f"  Research → {TRIGGERS_PATH.relative_to(VAULT_PATH)}/")
    log.info("Press Ctrl+C to stop.")
    log.info("=" * 60)

    handler = VaultHandler()
    observer = Observer()
    observer.schedule(handler, str(VAULT_PATH), recursive=WATCH_RECURSIVE)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping...")
        observer.stop()

    observer.join()
    log.info("Watchdog stopped.")


if __name__ == "__main__":
    main()
