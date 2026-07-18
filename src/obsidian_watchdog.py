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
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Add src to path so relative imports work when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    VAULT_PATH, INBOX_PATH, TRIGGERS_PATH, RESEARCH_PATH, SOURCES_PATH, ALL_PATHS,
    ICLOUD_SETTLE_SECS, WATCH_RECURSIVE, GEMINI_API_KEY, RESCAN_INTERVAL_SECS,
    PDF_INBOX_PATH, PDF_ARCHIVE_PATH,
)
from clipper import process_clipped_note, find_unprocessed_clips
from researcher import (
    process_research_trigger, find_pending_triggers,
    find_research_callouts, process_research_callout,
)
from pdf_processor import process_pdf, find_unprocessed_pdfs

# Serializes all pipeline work between the observer's event thread and the main
# thread's periodic rescan. Without it, a research run writing source clips to
# Clippings/ races the rescan, which can process-and-rename the same clip first
# and crash the research run mid-flight (FileNotFoundError on rename).
PIPELINE_LOCK = threading.Lock()

# ── Logging ───────────────────────────────────────────────────────────────────
# The whole pipeline diagnoses itself through print() (provider fall-through, the
# [research] phase trace, tool calls, errors). A plain FileHandler only captured
# the watchdog's own log.* calls, so the persistent log was near-useless for
# diagnosing a run — everything interesting went to stdout and vanished. We fix
# that by (1) rotating file + console handlers bound to the *real* stdout, then
# (2) redirecting sys.stdout/stderr into the logger so every print() is persisted,
# timestamped, and flushed per line — so the log is complete even if the process
# is killed mid-run. Handlers flush on each emit, so nothing is lost on a crash.
_real_stdout = sys.stdout  # captured before redirection to avoid a write-back loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(_real_stdout),
        RotatingFileHandler(
            VAULT_PATH.parent / "watchdog.log",
            maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)


class _StreamToLogger:
    """
    File-like shim that turns writes (print(), sys.stdout.write) into log records,
    so the entire pipeline's stdout/stderr lands in watchdog.log — not just the
    watchdog's own log.* calls. Bound to the real console via the logging handlers
    above, so there is no write-back loop.
    """

    encoding = "utf-8"

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, msg: str) -> int:
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(msg)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.log(self._level, self._buf)
        self._buf = ""

    def isatty(self) -> bool:
        return False


# Capture everything the pipeline prints. stdout → INFO, stderr → ERROR.
_pipeline_logger = logging.getLogger("pipeline")
sys.stdout = _StreamToLogger(_pipeline_logger, logging.INFO)
sys.stderr = _StreamToLogger(_pipeline_logger, logging.ERROR)


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

    PDF_INBOX_PATH.mkdir(parents=True, exist_ok=True)
    PDF_ARCHIVE_PATH.mkdir(parents=True, exist_ok=True)
    log.info(f"Ready: {PDF_INBOX_PATH}/")

    # Process any backlog from previous sessions
    unprocessed = find_unprocessed_clips(INBOX_PATH)
    if unprocessed:
        log.info(f"Found {len(unprocessed)} unprocessed clip(s) from previous sessions")
        for path in unprocessed:
            try:
                log.info(f"Processing backlog: {path.name}")
                process_clipped_note(path)
            except Exception as e:
                log.error(f"Error processing backlog {path.name}: {e}")
            time.sleep(3)

    unprocessed_pdfs = find_unprocessed_pdfs(PDF_INBOX_PATH)
    if unprocessed_pdfs:
        log.info(f"Found {len(unprocessed_pdfs)} unprocessed PDF(s) from previous sessions")
        for path in unprocessed_pdfs:
            try:
                log.info(f"Processing backlog: {path.name}")
                process_pdf(path)
            except Exception as e:
                log.error(f"Error processing backlog {path.name}: {e}")
            time.sleep(3)

    pending_triggers = find_pending_triggers(TRIGGERS_PATH)
    if pending_triggers:
        log.info(f"Found {len(pending_triggers)} pending research trigger(s) from previous sessions")
        for path in pending_triggers:
            try:
                log.info(f"Processing backlog trigger: {path.name}")
                process_research_trigger(path)
            except Exception as e:
                log.error(f"Error processing backlog trigger {path.name}: {e}")
            time.sleep(3)


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
        if path.suffix.lower() not in (".md", ".pdf"):
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
        """Route a new file to the appropriate pipeline."""
        try:
            parents = set(path.parents)

            with PIPELINE_LOCK:
                if path.suffix.lower() == ".pdf" and path.parent == PDF_INBOX_PATH:
                    log.info(f"New PDF detected: {path.name}")
                    process_pdf(path)

                elif INBOX_PATH in parents:
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
    log.info(f"  PDFs     → {PDF_INBOX_PATH}/")
    log.info("Press Ctrl+C to stop.")
    log.info("=" * 60)

    handler = VaultHandler()
    observer = Observer()
    observer.schedule(handler, str(VAULT_PATH), recursive=WATCH_RECURSIVE)
    observer.schedule(handler, str(PDF_INBOX_PATH), recursive=False)
    observer.start()

    last_rescan = time.time()
    try:
        while True:
            time.sleep(1)
            
            # Periodic re-scan for new files (iCloud doesn't fire events reliably)
            if time.time() - last_rescan >= RESCAN_INTERVAL_SECS:
                last_rescan = time.time()
                log.info("Periodic re-scan...")
                for path in find_unprocessed_clips(INBOX_PATH):
                    try:
                        log.info(f"Processing: {path.name}")
                        with PIPELINE_LOCK:
                            process_clipped_note(path)
                    except Exception as e:
                        log.error(f"Error processing {path.name}: {e}")
                    time.sleep(3)
                for path in find_unprocessed_pdfs(PDF_INBOX_PATH):
                    try:
                        log.info(f"Processing: {path.name}")
                        with PIPELINE_LOCK:
                            process_pdf(path)
                    except Exception as e:
                        log.error(f"Error processing {path.name}: {e}")
                    time.sleep(3)
                # Triggers whose create event was missed or whose run crashed
                # (iCloud events are unreliable — same reason clips are rescanned).
                for path in find_pending_triggers(TRIGGERS_PATH):
                    try:
                        log.info(f"Processing trigger: {path.name}")
                        with PIPELINE_LOCK:
                            process_research_trigger(path)
                    except Exception as e:
                        log.error(f"Trigger error {path.name}: {e}")
                    time.sleep(3)
                # Callouts can live in ANY note in the vault — they're answered
                # in place, like a review comment (see find_research_callouts).
                for cpath, ctopic in find_research_callouts([VAULT_PATH]):
                    try:
                        log.info(f"Processing callout: '{ctopic}' in {cpath.name}")
                        with PIPELINE_LOCK:
                            process_research_callout(cpath, ctopic)
                    except Exception as e:
                        log.error(f"Callout error in {cpath.name}: {e}")
                    time.sleep(3)
    except KeyboardInterrupt:
        log.info("Stopping...")
        observer.stop()

    observer.join()
    log.info("Watchdog stopped.")


if __name__ == "__main__":
    main()
