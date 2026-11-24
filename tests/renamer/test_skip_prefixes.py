"""Skip-rename prefix filter (owner, 2026-11-24): files or series folders starting with a configured prefix (e.g. ``@``)
are excluded from renaming and never count as rename candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist.models import FileHit, MangaEntry
from mangalist.parsing import parse_name
from mangalist.renamer import (
    ENFORCE_ASK,
    ENFORCE_AUTOMATIC,
    EXCLUDED,
    RENAME,
    UNCHANGED,
    Renamer,
    format_skip_prefixes,
    matching_skip_prefix,
    normalize_skip_prefixes,
    plan_files,
    plan_folder,
    set_skip_prefixes,
    skip_prefixes,
)

from .conftest import fmd2, make_library, payload
from .fakes import FakeNamer, Recorder


def test_normalize_and_persist_skip_prefixes(db):
    assert normalize_skip_prefixes("") == ()
    assert normalize_skip_prefixes(None) == ()
    assert normalize_skip_prefixes(" @ ") == ("@",)
    assert normalize_skip_prefixes("@, _, @  !") == ("@", "_", "!")
    assert normalize_skip_prefixes(["@", "  _ ", "@"]) == ("@", "_")
    assert format_skip_prefixes(("@", "_")) == "@, _"
    assert format_skip_prefixes(()) == ""

    with pytest.raises(ValueError, match="cannot contain"):
        normalize_skip_prefixes("@/bad")
    with pytest.raises(ValueError, match="cannot contain"):
        normalize_skip_prefixes(r"_\bad")

    assert skip_prefixes(db) == ()
    assert set_skip_prefixes(db, "@, _") == ("@", "_")
    assert skip_prefixes(db) == ("@", "_")
    assert set_skip_prefixes(db, "") == ()
    assert skip_prefixes(db) == ()


def test_matching_skip_prefix_checks_basename():
    assert matching_skip_prefix("@Special Vol 1.cbz", ("@", "_")) == "@"
    assert matching_skip_prefix("sub/@Special.cbz", ("@",)) == "@"
    assert matching_skip_prefix("_Draft.cbz", ("@", "_")) == "_"
    assert matching_skip_prefix("Normal @Tag.cbz", ("@",)) is None
    assert matching_skip_prefix("@Special.cbz", ()) is None


def test_plan_files_and_folder_mark_matching_prefixes_excluded(tmp_path):
    kept = "@Keep Me ch01.cbz"
    normal = fmd2(1, "0001", "Opening", "Group A")
    already = "Ch. 0002.00 [Group A].cbz"
    files = []
    for name in (kept, normal, already):
        p = tmp_path / name
        p.write_bytes(b"x" * 10)
        files.append((str(p), parse_name(name)))

    plans = {p.name: p for p in plan_files(files, namer=FakeNamer(), series_title="Series X", skip_prefixes=("@",))}
    assert plans[kept].status == EXCLUDED and plans[kept].excluded
    assert plans[kept].target is None and plans[kept].reason == "starts with '@'"
    assert plans[normal].status == RENAME and plans[normal].target == "Ch. 0001.00 (Opening) [Group A].cbz"
    assert plans[already].status == UNCHANGED

    status, reason = plan_folder("@Pinned Series", "Pinned Series [English]", names=set(), skip_prefixes=("@",))
    assert status == EXCLUDED and reason == "starts with '@'"


def test_renamer_excludes_prefixed_files_from_counts_apply_and_automatic_pass(db, tmp_path):
    lib = tmp_path / "Manga"
    lib.mkdir()
    kept = "@Special 001.cbz"
    normal = fmd2(1, "0001", "First", "Group A")
    only_kept = "@OneShot.cbz"
    root = make_library(db, lib, {
        "Series A": {kept: payload("k1"), normal: payload("n1")},
        "Series B": {only_kept: payload("b1")},
        "@Pinned": {fmd2(2, "0002", "Two", "Group A"): payload("p1")},
    })
    set_skip_prefixes(db, ("@",))
    hooks = Recorder()
    r = Renamer(db, namer=FakeNamer(), rescan=hooks.rescan, request_scans=hooks.request_scans,
                title_for=lambda _root, s: ("Pinned Romaji", ()) if s.rel_path == "@Pinned" else None)

    dry = r.dry_run(root_ids=[root.id])
    by_title = {s.title: s for s in dry.series}
    assert len(by_title["Series A"].renames) == 1 and len(by_title["Series A"].excluded) == 1
    assert len(by_title["Series B"].renames) == 0 and len(by_title["Series B"].excluded) == 1
    assert by_title["@Pinned"].folder_status == EXCLUDED and not by_title["@Pinned"].folder_rename
    assert len(by_title["@Pinned"].renames) == 1   # file inside @Pinned does not start with '@'

    root.enforce_naming = ENFORCE_ASK
    db.update_root(root)
    entries = [
        MangaEntry(
            folder=lib / name,
            title=name,
            english_title=None,
            root_id=root.id,
            files=[FileHit(p, p.stat().st_size, 0, parsed=parse_name(p.name)) for p in sorted((lib / name).iterdir())],
        )
        for name in ("Series A", "Series B")
    ]
    counts = r.pending_counts(entries)
    assert counts == {str(lib / "Series A"): 1}

    series_a = db.get_series(root.id, "Series A")
    assert r.scheme_name(root, series_a, kept) is None
    assert r.scheme_name(root, series_a, normal) == "Ch. 0001.00 (First) [Group A].cbz"

    batches = r.batches([by_title["Series A"], by_title["Series B"]])
    assert len(batches) == 1
    res = r.apply(batches[0])
    assert res.status == "applied" and res.renamed == 1
    assert (lib / "Series A" / kept).exists()
    assert (lib / "Series A" / "Ch. 0001.00 (First) [Group A].cbz").exists()
    assert (lib / "Series B" / only_kept).exists()

    # Changing skip_prefixes invalidates the automatic-pass cache so previously excluded files get reconsidered
    root.enforce_naming = ENFORCE_AUTOMATIC
    db.update_root(root)
    r_auto = Renamer(db, namer=FakeNamer(), rescan=hooks.rescan, request_scans=hooks.request_scans)
    r_auto.mark_guided([root.id])
    first_pass = r_auto.automatic_pass()
    assert first_pass.renamed == 1   # @Pinned's file renamed; @Special and @OneShot skipped
    assert (lib / "Series B" / only_kept).exists()
    second_pass = r_auto.automatic_pass()
    assert second_pass.renamed == 0 and second_pass.unchanged >= 1

    # Clearing skip_prefixes invalidates the cache on the next automatic pass
    set_skip_prefixes(db, ())
    dry_after = r_auto.dry_run(series_ids=[by_title["Series B"].series_id])
    assert len(dry_after.series[0].excluded) == 0
