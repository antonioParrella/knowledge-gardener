"""
Shared test setup.

The src/ modules import each other by bare name (`from notes import ...`,
`from config import ...`), so src/ must be on sys.path for the suite to import
anything. Do that here, once, before any test module is collected.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolate_telemetry(tmp_path, monkeypatch):
    """
    Point telemetry at a throwaway directory for EVERY test, and mute ntfy.

    Autouse and unconditional on purpose: telemetry's paths are derived from the
    real VAULT_PATH at import, and any test that exercises a pipeline function
    would otherwise write run state (and push phone notifications) into the live
    vault's parent directory.
    """
    import telemetry
    import notify

    before = (telemetry._state_path, telemetry._events_path)
    telemetry.configure(tmp_path / "telemetry")
    monkeypatch.setattr(notify, "NTFY_TOPIC", "", raising=False)
    yield telemetry
    telemetry.configure(state_path=before[0], events_path=before[1])


@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """
    A throwaway vault on disk with the standard folder layout, wired into every
    module that reads config path constants.

    The path constants (INBOX_PATH, INDEX_PATH, …) are module-level Paths computed
    from VAULT_PATH at import time and *copied* into each module's namespace by
    `from config import INBOX_PATH`. Rebinding only config.INBOX_PATH wouldn't
    reach the copy clipper/reset_clips already hold, so we patch every module that
    imported the name. Returns the vault root; sub-folders are attributes.
    """
    import config

    vault = tmp_path / "vault"
    folders = {
        "INBOX_PATH": vault / "Clippings",
        "INDEX_PATH": vault / "Index",
        "SOURCES_PATH": vault / "Sources",
        "RESEARCH_PATH": vault / "Research",
        "CONCEPTS_PATH": vault / "Concepts",
        "TRIGGERS_PATH": vault / "_triggers",
    }
    for p in folders.values():
        p.mkdir(parents=True, exist_ok=True)

    # Patch VAULT_PATH plus each derived constant everywhere it was imported.
    targets = ["config"]
    for modname in ("clipper", "reset_clips", "indexer"):
        try:
            __import__(modname)
            targets.append(modname)
        except Exception:
            pass  # module not importable in this environment — skip patching it

    monkeypatch.setattr(config, "VAULT_PATH", vault, raising=False)
    for name, path in folders.items():
        for modname in targets:
            mod = sys.modules.get(modname)
            if mod is not None and hasattr(mod, name):
                monkeypatch.setattr(mod, name, path, raising=False)

    return SimpleNamespace(root=vault, **folders)  # vault.root, vault.INBOX_PATH, …
