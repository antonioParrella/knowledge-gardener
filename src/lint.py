"""
lint.py — Periodic vault health checker.

Scans the Obsidian vault for common issues without using the Gemini API.
Run standalone or schedule via Windows Task Scheduler.

Usage:
    python src/lint.py           # full report to console
    python src/lint.py --quiet   # only print if issues found
    python src/lint.py --fix     # auto-fix what can be fixed

Checks:
    1. Duplicate source URLs across Clippings/ and Sources/
    2. Broken / unparseable YAML frontmatter
    3. MOC note_count mismatch vs actual [[links]]
    4. Orphan wikilinks in MOCs (pointing to missing notes)
    5. Duplicate [[links]] within a single MOC
    8. Notes with empty body content
    9. _index.md references to MOCs that don't exist
"""

import re
import sys
import yaml
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    VAULT_PATH, INBOX_PATH, RESEARCH_PATH, INDEX_PATH,
    SOURCES_PATH, TRIGGERS_PATH, LINT_REPORT_PATH,
)
from notes import read_note, write_note

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]+)?\]\]")


def _md_files(*paths: Path):
    """Yield all .md files across the given paths, skipping directories."""
    for p in paths:
        if not p.exists():
            continue
        for f in p.glob("*.md"):
            if f.is_file():
                yield f


def _raw_frontmatter(path: Path) -> tuple[bool, str]:
    """
    Try to extract the raw YAML frontmatter block from a file.
    Returns (ok, error_message).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Cannot read file: {e}"

    if not text.startswith("---"):
        return False, "No frontmatter block (does not start with '---')"

    try:
        _, fm_raw, _ = text.split("---", 2)
        yaml.safe_load(fm_raw)
        return True, ""
    except ValueError:
        return False, "Frontmatter delimiters malformed (missing opening/closing ---)"
    except yaml.YAMLError as e:
        return False, f"YAML parse error: {e}"
    except Exception as e:
        return False, f"Unexpected parse error: {e}"


def _wikilinks_in_section(body: str, section: str = "## Notes") -> list[str]:
    """Extract wikilink stems from a named section of a markdown body."""
    idx = body.find(section)
    if idx == -1:
        return []

    section_text = body[idx + len(section):]
    next_section = re.search(r"\n## ", section_text)
    if next_section:
        section_text = section_text[:next_section.start()]

    return WIKILINK_RE.findall(section_text)


def _all_note_stems() -> set[str]:
    """Collect every .md stem across the vault for orphan-checking."""
    stems = set()
    for f in _md_files(INBOX_PATH, RESEARCH_PATH, INDEX_PATH, SOURCES_PATH, TRIGGERS_PATH):
        stems.add(f.stem)
    return stems


# ═══════════════════════════════════════════════════════════════════
#  Individual checks
# ═══════════════════════════════════════════════════════════════════

def _pick_duplicate_to_delete(files: list[Path]) -> Path:
    """
    Given a list of files with the same source URL, pick the one to delete.
    Prefers iCloud ghost files, then 'Copy' in name, then unprocessed, then newest.
    """
    ghosts = [f for f in files if not f.exists()]
    if ghosts:
        return ghosts[0]

    copies = [f for f in files if " - Copy" in f.stem or " copy" in f.stem.lower()]
    if copies:
        return copies[0]

    unprocessed = [f for f in files if read_note(f)[0].get("processed") is not True]
    if unprocessed:
        return unprocessed[0]

    return max(files, key=lambda f: f.stat().st_mtime)


def check_duplicate_sources():
    issues = []
    url_map = defaultdict(list)

    for fp in _md_files(INBOX_PATH, SOURCES_PATH):
        fm, _ = read_note(fp)
        url = fm.get("source", "").strip()
        if url and url != "unknown":
            url_map[url].append(fp)

    for url, files in url_map.items():
        if len(files) > 1:
            names = [str(f.relative_to(VAULT_PATH)) for f in files]
            delete = _pick_duplicate_to_delete(files)
            issues.append({
                "severity": "warn",
                "path": files[0],
                "message": f"Duplicate source URL ({len(files)} copies): {url}",
                "detail": "\n    ".join(names),
                "fixable": True,
                "fix_data": {"delete": delete},
            })

    return issues


def check_broken_frontmatter():
    issues = []
    for fp in _md_files(INBOX_PATH, RESEARCH_PATH, INDEX_PATH, SOURCES_PATH, TRIGGERS_PATH):
        if fp.stem == "_index":
            continue
        ok, msg = _raw_frontmatter(fp)
        if not ok:
            issues.append({
                "severity": "error",
                "path": fp,
                "message": f"Broken frontmatter: {msg}",
                "detail": None,
            })
    return issues


def check_moc_note_count():
    issues = []
    for fp in _md_files(INDEX_PATH):
        if not fp.stem.startswith("MOC - "):
            continue
        fm, body = read_note(fp)
        expected = int(fm.get("note_count", 0))
        links = _wikilinks_in_section(body, "## Notes")
        actual = len(links)
        if expected != actual:
            issues.append({
                "severity": "warn",
                "path": fp,
                "message": (
                    f"MOC note_count={expected} but found {actual} [[links]] "
                    f"under ## Notes"
                ),
                "detail": None,
                "fixable": True,
                "fix_data": {"expected": actual},
            })
    return issues


def check_orphan_wikilinks():
    issues = []
    stems = _all_note_stems()
    for fp in _md_files(INDEX_PATH):
        if not fp.stem.startswith("MOC - "):
            continue
        _, body = read_note(fp)
        links = _wikilinks_in_section(body, "## Notes")
        for link_stem in links:
            if link_stem not in stems:
                issues.append({
                    "severity": "error",
                    "path": fp,
                    "message": (
                        f"Orphan wikilink [[{link_stem}]] — "
                        f"no matching .md found anywhere in vault"
                    ),
                    "detail": None,
                    "fixable": True,
                    "fix_data": {"dead_link": link_stem},
                })
    return issues


def check_duplicate_moc_entries():
    issues = []
    for fp in _md_files(INDEX_PATH):
        if not fp.stem.startswith("MOC - "):
            continue
        _, body = read_note(fp)
        links = _wikilinks_in_section(body, "## Notes")
        seen = set()
        for link_stem in links:
            if link_stem in seen:
                issues.append({
                    "severity": "warn",
                    "path": fp,
                    "message": f"Duplicate entry [[{link_stem}]] in MOC",
                    "detail": None,
                    "fixable": True,
                })
            else:
                seen.add(link_stem)
    return issues


def check_empty_body():
    issues = []
    for fp in _md_files(INBOX_PATH, RESEARCH_PATH, SOURCES_PATH, TRIGGERS_PATH):
        try:
            text = fp.read_text(encoding="utf-8")
            if text.startswith("---"):
                _, _, body = text.split("---", 2)
            else:
                body = text
            if not body.strip():
                issues.append({
                    "severity": "warn",
                    "path": fp,
                    "message": "Empty body — no content after frontmatter",
                    "detail": None,
                })
        except Exception:
            pass
    return issues


def check_stale_index():
    issues = []
    idx_path = INDEX_PATH / "_index.md"
    if not idx_path.exists():
        return issues

    text = idx_path.read_text(encoding="utf-8")
    moc_links = re.findall(r"\[\[(MOC - [^\]]+)\]\]", text)
    for link_name in moc_links:
        moc_file = INDEX_PATH / f"{link_name}.md"
        if not moc_file.exists():
            issues.append({
                "severity": "warn",
                "path": idx_path,
                "message": (
                    f"_index.md references [[{link_name}]] "
                    f"but {link_name}.md does not exist"
                ),
                "detail": None,
                "fixable": True,
                "fix_data": {"dead_link": f"[[{link_name}]]"},
            })
    return issues


# ═══════════════════════════════════════════════════════════════════
#  Aggregation & reporting
# ═══════════════════════════════════════════════════════════════════

def run_all():
    """Run all checks and return a dict of check_name -> issue_list."""
    return {
        "duplicate_sources":  check_duplicate_sources(),
        "broken_frontmatter": check_broken_frontmatter(),
        "moc_note_count":     check_moc_note_count(),
        "orphan_wikilinks":   check_orphan_wikilinks(),
        "duplicate_moc_entries": check_duplicate_moc_entries(),
        "empty_body":         check_empty_body(),
        "stale_index":        check_stale_index(),
    }


def report(results: dict, quiet: bool = False):
    """Print a formatted report to stdout."""
    total = sum(len(v) for v in results.values())
    lines = []
    lines.append(f"Vault lint report for: {VAULT_PATH}")
    lines.append(f"Total issues found: {total}")
    lines.append("=" * 60)

    if total == 0:
        lines.append("All clear — no issues detected.")
    else:
        check_labels = {
            "duplicate_sources":     "Duplicate source URLs",
            "broken_frontmatter":    "Broken YAML frontmatter",
            "moc_note_count":        "MOC note_count mismatch",
            "orphan_wikilinks":      "Orphan wikilinks in MOCs",
            "duplicate_moc_entries": "Duplicate MOC entries",
            "empty_body":            "Empty body notes",
            "stale_index":           "Stale _index.md references",
        }
        for check_name, issues in results.items():
            if not issues:
                continue
            lines.append(f"\n--- {check_labels[check_name]} ({len(issues)}) ---")
            for i, issue in enumerate(issues, 1):
                rel = issue["path"].relative_to(VAULT_PATH)
                sev = issue["severity"].upper()
                lines.append(f"  [{sev}] {rel}")
                lines.append(f"         {issue['message']}")
                if issue.get("detail"):
                    lines.append(f"         {issue['detail']}")

    lines.append("=" * 60)
    report_text = "\n".join(lines)

    if not (quiet and total == 0):
        print(report_text)

    if LINT_REPORT_PATH:
        LINT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        LINT_REPORT_PATH.write_text(report_text + "\n", encoding="utf-8")


def auto_fix(results: dict):
    """Attempt to auto-fix fixable issues."""
    fixed = 0

    for check_name, issues in results.items():
        for issue in issues:
            if not issue.get("fixable"):
                continue

            if check_name == "moc_note_count":
                fp = issue["path"]
                new_count = issue["fix_data"]["expected"]
                fm, body = read_note(fp)
                fm["note_count"] = new_count
                write_note(fp, fm, body)
                print(f"[fix] Updated note_count to {new_count} in {fp.stem}")
                fixed += 1

            elif check_name == "stale_index":
                fp = issue["path"]
                dead_link = issue["fix_data"]["dead_link"]
                text = fp.read_text(encoding="utf-8")
                new_text = re.sub(
                    r"^- " + re.escape(dead_link) + r"\s*\n?",
                    "",
                    text,
                    flags=re.MULTILINE,
                )
                fp.write_text(new_text, encoding="utf-8")
                print(f"[fix] Removed stale reference {dead_link} from _index.md")
                fixed += 1

            elif check_name == "duplicate_moc_entries":
                fp = issue["path"]
                _, body = read_note(fp)
                fm = read_note(fp)[0]
                links = _wikilinks_in_section(body, "## Notes")
                seen = set()
                deduped_links = []
                for link in links:
                    if link not in seen:
                        deduped_links.append(link)
                        seen.add(link)
                rebuilt = "\n".join(f"- [[{l}]]" for l in deduped_links)
                new_body = body
                new_body = re.sub(
                    r"(## Notes\n).*?(\n## |$)",
                    rf"\1{rebuilt}\n\2",
                    new_body,
                    flags=re.DOTALL,
                    count=1,
                )
                if "## Notes\n" in new_body and rebuilt not in new_body:
                    new_body = new_body.replace("## Notes\n", f"## Notes\n{rebuilt}\n\n")
                fm["note_count"] = len(deduped_links)
                write_note(fp, fm, new_body.strip())
                print(f"[fix] Deduplicated entries in {fp.stem}")
                fixed += 1

            elif check_name == "orphan_wikilinks":
                fp = issue["path"]
                dead_link = issue["fix_data"]["dead_link"]
                fm, body = read_note(fp)
                pattern = r"^- \[\[" + re.escape(dead_link) + r"\]\].*?\n"
                new_body = re.sub(pattern, "", body, flags=re.MULTILINE)
                remaining = _wikilinks_in_section(new_body, "## Notes")
                fm["note_count"] = len(remaining)
                write_note(fp, fm, new_body.strip())
                print(f"[fix] Removed orphan [[{dead_link}]] from {fp.stem}")
                fixed += 1

            elif check_name == "duplicate_sources":
                delete_path = issue["fix_data"]["delete"]
                if delete_path.exists():
                    delete_path.unlink()
                    print(f"[fix] Deleted duplicate: {delete_path.stem}")
                    fixed += 1
                else:
                    print(f"[fix] Skipped (already gone): {delete_path.stem}")

    if fixed == 0:
        print("[fix] No auto-fixable issues found.")
    else:
        print(f"[fix] Fixed {fixed} issue(s). Run lint.py again to verify.")


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = set(sys.argv[1:])
    quiet = "--quiet" in args or "-q" in args
    fix = "--fix" in args

    results = run_all()

    if fix:
        auto_fix(results)
        results = run_all()

    report(results, quiet=quiet)
    sys.exit(min(sum(len(v) for v in results.values()), 1))
