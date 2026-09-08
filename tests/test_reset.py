import sqlite3

from caregiver import db, demo
from caregiver.importers import ccda
from caregiver.web import queries as Q

from test_importers import FIX


def test_reset_demo_keeps_real_data_and_notes(cfg, conn):
    demo.load(cfg, conn)
    ccda.import_path(cfg, conn, FIX / "sample_ccda.xml")
    Q.add_note(conn, "question", "Ask about the dex dose", "real caregiver note")

    before = db.source_breakdown(conn)
    assert before["demo"] > 400 and before["caregiver"] == 1
    ccda_key = next(k for k in before if k.startswith("ccda:"))

    removed = db.delete_source(conn, "demo")
    assert removed["observation"] > 400 and removed["note"] == 6  # demo seed notes go too

    after = db.source_breakdown(conn)
    assert "demo" not in after
    assert after[ccda_key] == before[ccda_key]
    assert after["caregiver"] == 1
    assert db.one(conn, "SELECT title FROM note")["title"] == "Ask about the dex dose"
    assert db.get_meta(conn, "demo") is None
    # the real C-CDA lab survived and is still classified
    assert db.one(conn, "SELECT panel_key FROM observation WHERE display='M-spike'")["panel_key"] == "m_protein"


def test_reset_demo_can_keep_all_notes(cfg, conn):
    demo.load(cfg, conn)
    db.delete_source(conn, "demo", keep_notes=True)
    assert db.counts(conn)["note"] == 6 and db.counts(conn)["observation"] == 0


def test_migration_adds_source_to_preexisting_database(tmp_path):
    """A database created before the note.source column must upgrade in place, not crash."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE note (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, updated TEXT, kind TEXT,
          title TEXT, body TEXT, status TEXT DEFAULT 'open', due TEXT, owner TEXT, related_type TEXT,
          related_id TEXT, event_date TEXT);
        INSERT INTO note (title, kind, status) VALUES ('pre-existing note', 'issue', 'open');
    """)
    old.commit()
    old.close()

    conn = db.connect(path)
    cols = {r[1] for r in conn.execute('PRAGMA table_info("note")')}
    assert "source" in cols
    row = db.one(conn, "SELECT title, source FROM note")
    assert row["title"] == "pre-existing note"
    # an existing note is never mistaken for demo data
    assert row["source"] != "demo"
    db.delete_source(conn, "demo")
    assert db.counts(conn)["note"] == 1
    conn.close()
