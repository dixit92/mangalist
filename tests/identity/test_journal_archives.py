"""Journal moves keep the archive rows in step directly (no detection needed), and undo follows back."""

from __future__ import annotations

from mangalist.store import Journal

from .conftest import make_archive, scan


def test_a_journal_folder_move_repoints_the_series_and_its_archive_rows(db, library):
    make_archive(library / "Old Title" / "Old Title v01.cbz")
    make_archive(library / "Old Title" / "Extras" / "Old Title v01 extra.cbz", size=5000)
    root = db.add_root(str(library))
    scan(db)
    sid = db.get_series(root.id, "Old Title").id
    ids = {a.rel_path: a.id for a in db.list_archives(root.id)}
    journal = Journal(db)
    plan = journal.plan("manual", [(library / "Old Title", library / "New Title")], root_path=library)
    assert journal.apply(plan.id).status == "applied"
    assert db.get_series(root.id, "New Title").id == sid
    after = {a.rel_path: (a.id, a.series_id) for a in db.list_archives(root.id)}
    assert after == {"New Title/Old Title v01.cbz": (ids["Old Title/Old Title v01.cbz"], sid),
                     "New Title/Extras/Old Title v01 extra.cbz": (ids["Old Title/Extras/Old Title v01 extra.cbz"], sid)}
    with db.connect() as con:
        hows = [r["how"] for r in con.execute("SELECT how FROM archive_moves")]
    assert hows == ["journal", "journal"]
    # The next scan sees nothing new and nothing missing.
    rep = scan(db).identity
    assert rep.archives_new == 0 and rep.archives_missing == 0
    assert journal.undo(plan.id).status == "undone"
    assert {a.rel_path for a in db.list_archives(root.id)} == set(ids)
    assert db.get_series(root.id, "Old Title").id == sid


def test_a_journal_file_move_keeps_the_archive_row(db, library):
    make_archive(library / "Series A" / "ch1.cbz")
    root = db.add_root(str(library))
    scan(db)
    aid = db.archive_at(root.id, "Series A/ch1.cbz").id
    journal = Journal(db)
    plan = journal.plan("enforcement", [(library / "Series A" / "ch1.cbz", library / "Series A" / "Series A c001.cbz")],
                        root_path=library)
    journal.apply(plan.id)
    moved = db.archive_at(root.id, "Series A/Series A c001.cbz")
    assert moved.id == aid and moved.series_id == db.get_series(root.id, "Series A").id
    assert scan(db).identity.archives_new == 0
