"""
Tier 2 — reset_clips.clean_mocs MOC surgery against a real Index/ folder.

Removing a clip's [[link]] from every MOC, decrementing note_count, and deleting
MOCs that hit zero is exactly the multi-file mutation that leaves orphans behind
when it drifts. Assert it on disk.
"""

import reset_clips
import notes


def _moc(index_path, topic, entries):
    """Write a MOC with the given [[Title]] entries under ## Notes."""
    path = index_path / f"MOC - {topic}.md"
    body = "# " + topic + " — Knowledge Index\n\n## Notes\n" + \
        "".join(f"- [[{e}]] — summary\n" for e in entries)
    notes.write_note(path, {"moc": True, "topic": topic, "note_count": len(entries)}, body)
    return path


def test_removes_entry_and_decrements_count(tmp_vault):
    moc = _moc(tmp_vault.INDEX_PATH, "Generative Models", ["Keep Me", "Drop Me"])
    reset_clips.clean_mocs({"Drop Me"})

    fm, body = notes.read_note(moc)
    assert "[[Drop Me]]" not in body
    assert "[[Keep Me]]" in body
    assert fm["note_count"] == 1


def test_deletes_moc_that_empties(tmp_vault):
    moc = _moc(tmp_vault.INDEX_PATH, "Solo Topic", ["Only Entry"])
    deleted = reset_clips.clean_mocs({"Only Entry"})

    assert not moc.exists()
    assert "Solo Topic" in deleted


def test_untouched_moc_left_alone(tmp_vault):
    moc = _moc(tmp_vault.INDEX_PATH, "Unrelated", ["Something Else"])
    before = moc.read_text(encoding="utf-8")
    reset_clips.clean_mocs({"Not In Any MOC"})
    assert moc.read_text(encoding="utf-8") == before


def test_clean_master_index_removes_deleted_moc_link(tmp_vault):
    master = tmp_vault.INDEX_PATH / "_index.md"
    master.write_text(
        "# Index\n\n- [[MOC - Gone Topic]]\n- [[MOC - Kept Topic]]\n",
        encoding="utf-8",
    )
    reset_clips.clean_master_index({"Gone Topic"})

    text = master.read_text(encoding="utf-8")
    assert "MOC - Gone Topic" not in text
    assert "MOC - Kept Topic" in text
