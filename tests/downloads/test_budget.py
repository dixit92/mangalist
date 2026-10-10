"""The download budget's accounting (mangalist.downloads.budget) and its setting: what counts, the verdict for one more
download, the wording. Made-up records only."""

from __future__ import annotations

from mangalist.downloads import budget as B
from mangalist.downloads.budget import GB
from mangalist.downloads.contracts import DownloadRecord, DownloadStatus as S
from mangalist.downloads.options import DEFAULT_BUDGET_GB, KEY_BUDGET_GB, get_budget_gb, set_budget_gb


def rec(id=1, status=S.SENT, size=10 * GB, *, in_client=True, place=0, source="release", error=None):
    return DownloadRecord(id=id, series_id=7, info_hash=f"{id:040x}", title=f"Release {id}", wanted_volumes=("3",),
                          target_dir="/lib/S", status=status, created_at="2026-10-09T10:00:00Z",
                          updated_at="2026-10-09T10:00:00Z", size_bytes=size, size_source=source, in_client=in_client,
                          queue_position=place, error=error)


def test_what_counts():
    assert [s for s in S.ALL if B.counts(rec(status=s))] == [S.SENT, S.DOWNLOADED, S.FILED, S.FAILED]
    assert not B.counts(rec(status=S.FAILED, in_client=False))          # seen gone from qBittorrent
    assert B.counts(rec(status=S.FILED, in_client=False))               # only a failed one can stop counting
    records = [rec(1, S.SENT, 1 * GB), rec(2, S.DOWNLOADED, 2 * GB), rec(3, S.FILED, 3 * GB), rec(4, S.REMOVED, 4 * GB),
               rec(5, S.FAILED, 5 * GB), rec(6, S.FAILED, 6 * GB, in_client=False), rec(7, S.CANCELLED, 7 * GB),
               rec(8, S.QUEUED, 8 * GB, place=1)]
    assert B.used_bytes(records) == 11 * GB


def test_state_and_verdicts():
    st = B.state_of([rec(1, S.FILED, 40 * GB), rec(2, S.QUEUED, 30 * GB, place=1)], 50)
    assert st.limited and st.used_bytes == 40 * GB and st.free_bytes == 10 * GB and not st.over
    assert [r.id for r in st.queued] == [2]
    assert st.verdict(5 * GB) == B.OVER                         # it would fit, but one waits ahead: first come first served
    assert st.verdict(5 * GB, ignore_queue=True) == B.FITS
    assert st.verdict(11 * GB, ignore_queue=True) == B.OVER
    assert st.verdict(51 * GB) == B.TOO_BIG
    assert st.verdict(50 * GB, ignore_queue=True) == B.OVER     # exactly the cap alone is not "too big"
    empty = B.state_of([rec(1, S.FILED, 40 * GB)], 50)
    assert empty.verdict(10 * GB) == B.FITS and empty.verdict(10 * GB + 1) == B.OVER


def test_a_queued_item_bigger_than_the_cap_never_holds_up_new_sends():
    st = B.state_of([rec(1, S.QUEUED, 80 * GB, place=1)], 50)
    assert st.waiting == () and st.verdict(5 * GB) == B.FITS
    assert B.first_waiting(st.queued, st) is None


def test_no_limit():
    st = B.state_of([rec(1, S.SENT, 900 * GB)], 0)
    assert not st.limited and st.fits(10 ** 15) and st.verdict(10 ** 15) == B.FITS and not st.over
    assert st.usage_text() == "using 900 GB, no limit"


def test_cap_lowered_below_usage_removes_nothing_and_queues_new_sends():
    records = [rec(1, S.FILED, 30 * GB), rec(2, S.SENT, 15 * GB)]
    st = B.state_of(records, 20)
    assert st.over and st.used_bytes == 45 * GB and st.free_bytes == 0
    assert st.verdict(1 * GB) == B.OVER
    assert [r.status for r in records] == [S.FILED, S.SENT]


def test_queue_order_is_the_queue_position_then_the_id():
    records = [rec(5, S.QUEUED, place=2), rec(3, S.QUEUED, place=3), rec(9, S.QUEUED, place=1), rec(1, S.SENT)]
    assert [r.id for r in B.queue_of(records)] == [9, 5, 3]


def test_wording():
    assert B.gb_text(50 * GB) == "50 GB" and B.gb_text(int(12.34 * GB)) == "12.3 GB" and B.gb_text(0) == "0 GB"
    st = B.state_of([rec(1, S.FILED, int(12.3 * GB)), rec(2, S.QUEUED, 1 * GB, place=1),
                     rec(3, S.SENT, 0, source="")], 50)
    assert st.usage_text() == "using 12.3 GB of 50 GB"
    assert st.summary() == "Using 12.3 GB of 50 GB; 1 download queued; 1 download of unknown size not counted"
    assert [B.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "111th"]
    assert B.place_text(2) == "2nd in line" and B.place_text(0) == "in the queue"
    assert B.size_note(2 * GB, B.SIZE_RELEASE).startswith("2 GB (the release's size on nyaa")
    assert B.size_note(2 * GB, B.SIZE_SELECTED).startswith("2 GB (the selected files")
    assert B.size_note(2 * GB, B.SIZE_CLIENT) == "2 GB (as qBittorrent reports it)"
    assert B.size_note(0, "") == "size not known yet"
    over = B.state_of([rec(1, S.FILED, 48 * GB)], 50)
    assert B.over_cap_text(over, 3 * GB) == ("This would go over the download budget: MangaList is using 48 GB of 50 GB, "
                                             "and this adds 3 GB.")
    behind = B.state_of([rec(1, S.FILED, 10 * GB), rec(2, S.QUEUED, 45 * GB, place=1)], 50)
    assert "1 download is already waiting in the queue" in B.over_cap_text(behind, 1 * GB)
    assert B.too_big_text(62 * GB, over).startswith("This release is 62 GB - bigger than the whole download budget of 50 GB")


class _Settings:
    def __init__(self):
        self.values = {}

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


def test_the_cap_setting():
    db = _Settings()
    assert get_budget_gb(db) == DEFAULT_BUDGET_GB == 50                 # owner, 2026-10-09: 50 GB by default
    set_budget_gb(db, 120)
    assert db.values[KEY_BUDGET_GB] == 120 and get_budget_gb(db) == 120
    set_budget_gb(db, 12.5)
    assert get_budget_gb(db) == 12.5
    for off in (0, None, -3):
        set_budget_gb(db, off)
        assert db.values[KEY_BUDGET_GB] == 0 and get_budget_gb(db) == 0            # 0 = no limit
    for bad in ("50", True, -1, 10 ** 9, [50]):
        db.values[KEY_BUDGET_GB] = bad
        assert get_budget_gb(db) == 50
