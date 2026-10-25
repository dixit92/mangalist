"""The real backend's side of other releases (owner, 2026-10-24): search_all returns the other releases after the usable
ones (search stays as it was, for the find pass), a send carries the volumes it replaces onto the record, and the
replacement preview names the files a replacement would move. Made-up names, a FAKE qBittorrent."""

from __future__ import annotations

from mangalist.downloads.adapter import Backend
from mangalist.downloads.contracts import DownloadStatus
from mangalist.gui.downloads_backend import QbtSettings
from mangalist.services.nyaa.rss import RssItem

from .fakes import candidate, data, scan


class _Nyaa:
    def __init__(self, feeds):
        self.feeds, self.asked = feeds, []

    def rss(self, query, category="3_1", filter_=0):
        self.asked.append((query, category))
        return list(self.feeds.get(category, []))


def _item(title, category="3_1", trusted=False, seeders=5):
    h = f"{abs(hash(title)):040x}"[:40]
    return RssItem(title=title, torrent_url=f"https://nyaa.example/dl/{h[:6]}", view_url=f"https://nyaa.example/{h[:6]}",
                   info_hash=h, published="2026-10-01T00:00:00+00:00", seeders=seeders, category=category,
                   trusted=trusted, size_bytes=1000)


def _backend(db, qbt, **kw):
    return Backend(db, client_factory=lambda conn: qbt, **kw)


def test_search_all_returns_the_other_releases_after_the_usable_ones(db, qbt):
    from mangalist.downloads.options import NyaaOptions

    client = _Nyaa({"3_1": [_item("Series A v01-03 (Digital) (New Group)"), _item("Series A v04 (Digital)")],
                    "3_3": [_item("Series A v01 (Raw)", "3_3", trusted=True)]})
    b = _backend(db, qbt, nyaa_client=client)
    assert [c.title for c in b.search(["Series A"], ["4"], ["1", "2", "3"])] == ["Series A v04 (Digital)"]
    every = b.search_all(["Series A"], ["4"], ["1", "2", "3"])
    assert [(c.title, c.other) for c in every] == [("Series A v04 (Digital)", False),
                                                   ("Series A v01-03 (Digital) (New Group)", True)]
    b.set_nyaa_options(NyaaOptions(english=True, raw=True))
    every = b.search_all(["Series A"], ["4"], ["1", "2", "3"])
    assert [c.other for c in every] == [False, True, True]            # two categories: usable first, still
    b.set_nyaa_options(NyaaOptions(english=True, raw=True, trusted_only=True))
    assert [c.title for c in b.search_all(["Series A"], ["4"], ["1", "2", "3"])] == ["Series A v01 (Raw)"]


def test_an_injected_search_without_other_releases_still_answers_search_all(db, qbt):
    class Plain:
        def search(self, titles, missing, held):
            return [candidate()]

    assert [c.info_hash for c in _backend(db, qbt, search=Plain()).search_all(["x"], [], [])] == [candidate().info_hash]


def test_a_send_carries_the_volumes_it_replaces(db, series, qbt, tmp_path):
    sid, sdir = series
    b = _backend(db, qbt)
    b.save_settings(QbtSettings(base_url="box:8080", username="admin", save_path=str(tmp_path / "torrents")), "pw")
    rec = b.send(sid, candidate(title="Series A v01-02 (Digital) (New Group)", vol_from="1", vol_to="2"), ["1", "2"],
                 str(sdir), replace_volumes=("1",))
    assert rec.status == DownloadStatus.SENT and rec.replace_volumes == ("1",)
    plain = b.send(sid, candidate("ef" * 20), ["3"], str(sdir))
    assert plain.replace_volumes == () and "replace_volumes" not in b.ledger.request(plain.id)


def test_a_queued_replacement_keeps_what_it_replaces(ledger, series):
    sid, sdir = series
    rec = ledger.create(sid, candidate(), ["1", "2"], str(sdir), status=DownloadStatus.QUEUED, replace_volumes=["2"])
    assert ledger.get(rec.id).replace_volumes == ("2",) and ledger.get(rec.id).status == DownloadStatus.QUEUED


def test_the_replacement_preview_names_the_files_moved_and_kept(db, library, qbt):
    (library / "Series A" / "Series A v02-03 (Digital).cbz").write_bytes(data("held v02-03"))
    root = db.add_root(str(library), "Manga")
    scan(db)
    sid = db.get_series(root.id, "Series A").id
    moved, kept = _backend(db, qbt).replacement_preview(sid, ["1", "2"])
    assert moved == ["Series A v01 (Digital).cbz"] and kept == ["Series A v02-03 (Digital).cbz"]
    assert _backend(db, qbt).replacement_preview(sid, ["2", "3"]) == (["Series A v02-03 (Digital).cbz"], [])
