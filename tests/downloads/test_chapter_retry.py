"""Chapter downloads Suwayomi gave up on (mangalist.downloads.chapter_retry; owner 2026-10-20: "I can't manually retry
this"): the arrivals pass notes them and puts them back in Suwayomi's queue every 6 h, at most 3 times; the owner's Retry
re-queues them now and starts over; Cancel takes them out of the queue and cancels the records. A FAKE Suwayomi, a fake
clock - no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mangalist.downloads import chapter_retry as cr
from mangalist.downloads.contracts import QueuedChapter, DownloadStatus as S
from mangalist.gui.downloads_backend import BackendError

from .test_chapter_arrivals import cledger, cseries, downloads, run, sent, suwa  # noqa: F401 - fixtures

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(cr, "utcnow", lambda: now["t"])
    return now


def give_up(suwa, cid=5, tries=3):
    """Suwayomi tried and gave up: the chapter sits in its queue in the ERROR state."""
    suwa.queued[cid] = QueuedChapter(cid, "ERROR", 0.0, tries)


def enqueues(suwa):
    return [ids for name, *rest in suwa.calls if name == "enqueue" for ids in rest]


def test_a_stuck_chapter_is_retried_every_6_h_at_most_3_times(cledger, sent, suwa, downloads, clock):
    rec = sent[1]                                    # chapter id 5
    give_up(suwa)
    before = len(enqueues(suwa))
    run(suwa, cledger, downloads)
    got = cledger.get(rec.id)
    assert cr.is_stuck(got) and "(3 tries)" in got.error and "retries it within 6 h" in got.error
    assert len(enqueues(suwa)) == before                     # just noticed: no retry yet
    clock["t"] = T0 + timedelta(hours=5)
    run(suwa, cledger, downloads)
    assert len(enqueues(suwa)) == before
    for n in (1, 2, 3):
        clock["t"] = T0 + timedelta(hours=6 * n)
        run(suwa, cledger, downloads)
        assert enqueues(suwa)[-1] == [5] and len(enqueues(suwa)) == before + n
        got = cledger.get(rec.id)
        assert got.status == S.SENT and not cr.is_stuck(got)   # back in Suwayomi's queue: nothing to offer
        assert f"MangaList retried it in Suwayomi ({n} of 3)" in got.error
        give_up(suwa, tries=3 + n)                               # ... and Suwayomi gives up again
        clock["t"] += timedelta(hours=1)
        run(suwa, cledger, downloads)
        got = cledger.get(rec.id)
        assert cr.is_stuck(got) and f"{3 + n} tries" in got.error
    assert "MangaList retried it 3 times) - Retry or Cancel" in got.error
    clock["t"] = T0 + timedelta(days=5)
    run(suwa, cledger, downloads)
    assert len(enqueues(suwa)) == before + 3                 # it waits for the owner now
    assert cr.load(cledger.store, rec.id).auto == 3


def test_a_chapter_that_downloads_or_runs_again_forgets_its_retries(cledger, sent, suwa, downloads, clock):
    give_up(suwa)
    run(suwa, cledger, downloads)
    assert cr.load(cledger.store, sent[1].id) is not None
    suwa.queued[5] = QueuedChapter(5, "DOWNLOADING", 0.2)   # retried in Suwayomi's own UI
    run(suwa, cledger, downloads)
    assert cr.load(cledger.store, sent[1].id) is None and cledger.get(sent[1].id).error is None
    give_up(suwa)
    run(suwa, cledger, downloads)
    suwa.finish(5)
    run(suwa, cledger, downloads)
    assert cr.load(cledger.store, sent[1].id) is None and cledger.get(sent[1].id).status != S.SENT


def test_suwayomi_refusing_the_automatic_retry_counts_nothing(cledger, sent, suwa, downloads, clock):
    give_up(suwa)
    run(suwa, cledger, downloads)
    suwa.enqueue_error = ConnectionError("Suwayomi is down")
    clock["t"] = T0 + timedelta(hours=7)
    run(suwa, cledger, downloads)
    got = cledger.get(sent[1].id)
    assert cr.is_stuck(got) and got.status == S.SENT
    assert cr.load(cledger.store, sent[1].id).auto == 0      # the next pass tries again


# --- the owner's Retry / Cancel -------------------------------------------------------------------------------------

def test_retry_by_hand_requeues_now_and_starts_the_automatic_retries_over(cledger, sent, suwa, downloads, clock):
    give_up(suwa)
    clock["t"] = T0 - timedelta(hours=12)
    run(suwa, cledger, downloads)                    # noticed 12 h ago ...
    clock["t"] = T0
    run(suwa, cledger, downloads)                    # ... and retried once automatically
    give_up(suwa, tries=4)
    run(suwa, cledger, downloads)
    assert cr.load(cledger.store, sent[1].id).auto == 1
    n = len(enqueues(suwa))
    assert cr.retry(cledger.store, suwa, [sent[0].id, sent[1].id]) == 1     # only the stuck one
    assert enqueues(suwa)[n:] == [[5]]
    got = cledger.get(sent[1].id)
    assert got.status == S.SENT and got.error == "retried by you; waiting for Suwayomi" and not cr.is_stuck(got)
    assert cr.load(cledger.store, sent[1].id) is None
    with pytest.raises(cr.RetryRefused):
        cr.retry(cledger.store, suwa, [sent[1].id])                         # queued again: nothing stuck


def test_cancel_takes_it_out_of_suwayomis_queue_and_cancels_the_record(cledger, sent, suwa, downloads, clock):
    give_up(suwa)
    run(suwa, cledger, downloads)
    assert cr.cancel(cledger.store, suwa, [sent[1].id]) == 1
    assert ("dequeue", [5]) in suwa.calls and 5 not in suwa.queued
    got = cledger.get(sent[1].id)
    assert got.status == S.CANCELLED and got.error.startswith("cancelled by you (Suwayomi could not download it")
    assert cr.load(cledger.store, sent[1].id) is None
    # transition audit B1: any chapter download still on its way can be cancelled, stuck or not
    assert not cr.is_stuck(cledger.get(sent[0].id)) and cr.can_cancel(cledger.get(sent[0].id))
    assert cr.cancel(cledger.store, suwa, [sent[0].id]) == 1
    assert ("dequeue", [4]) in suwa.calls and cledger.get(sent[0].id).status == S.CANCELLED
    with pytest.raises(cr.RetryRefused):
        cr.cancel(cledger.store, suwa, [sent[0].id])                        # cancelled already


def test_cancel_works_when_the_program_cannot_be_asked(cledger, sent, suwa, downloads, clock):
    # the switch guard says "cancel them": with the program stopped (or not chosen any more) that must still work
    suwa.unreachable = True
    assert cr.cancel(cledger.store, suwa, [sent[0].id]) == 1
    got = cledger.get(sent[0].id)
    assert got.status == S.CANCELLED and "Suwayomi could not be asked" in got.error and "may still download" in got.error
    assert cr.cancel(cledger.store, None, [sent[1].id], backend="suwayomi") == 1
    assert "Suwayomi was not asked" in cledger.get(sent[1].id).error


def test_the_backend_wraps_retry_and_cancel(db, cledger, sent, suwa, downloads, clock, monkeypatch):
    from mangalist.downloads.adapter import Backend
    from mangalist.downloads.contracts import SuwayomiConnection
    from mangalist.store.downloads import SuwayomiSettings

    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.invalid:4567"))
    give_up(suwa)
    run(suwa, cledger, downloads)
    backend = Backend(db)
    monkeypatch.setattr(backend, "_chapter_client", lambda *a, **k: suwa)
    assert backend.retry_chapter_downloads([sent[1].id]) == 1
    with pytest.raises(BackendError, match="no longer stuck"):
        backend.retry_chapter_downloads([sent[1].id])
    assert backend.cancel_chapter_downloads([sent[1].id]) == 1          # queued again, still cancellable
    with pytest.raises(BackendError, match="no longer on its way"):
        backend.cancel_chapter_downloads([sent[1].id])
    SuwayomiSettings(db).forget_connection()
    with pytest.raises(BackendError, match="not set up"):
        backend.retry_chapter_downloads([sent[0].id])
    assert backend.cancel_chapter_downloads([sent[0].id]) == 1          # cancelled without asking anyone


# --- transition audit B1: the built-in extension host's stuck chapters ----------------------------------------------

class HostFake:
    """The fake program, answering as the built-in extension host (its client's ``backend``)."""

    backend = "extensions"

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def host_sent(cledger, cseries, suwa):
    from mangalist.downloads import chapters as chm

    from .test_chapter_arrivals import MANGA, MANGADEX

    sid, folder = cseries
    out = chm.send_chapters(HostFake(suwa), cledger, sid, chm.MangaMatch(MANGADEX, MANGA, chm.HOW_MANGADEX),
                            suwa.chapter_list, str(folder))
    return out.records


def test_a_chapter_the_extension_host_gave_up_on_offers_retry_and_cancel(cledger, host_sent, suwa, downloads, clock):
    host = HostFake(suwa)
    assert {r.backend for r in host_sent} == {"extensions"}
    give_up(suwa)
    run(host, cledger, downloads)
    got = cledger.get(host_sent[1].id)
    # the note names the host (its words), and the record is stuck by its STATE - the old note test did not match
    assert got.error.startswith("The extension host could not download it") and not got.error.startswith(cr.STUCK)
    assert got.stuck and cr.is_stuck(got)
    assert cr.retry(cledger.store, host, [got.id]) == 1
    again = cledger.get(got.id)
    assert again.error == "retried by you; waiting for the extension host" and not cr.is_stuck(again)
    give_up(suwa)
    run(host, cledger, downloads)
    assert cr.is_stuck(cledger.get(got.id))
    assert cr.cancel(cledger.store, host, [got.id]) == 1
    assert cledger.get(got.id).error.startswith("cancelled by you (The extension host could not download it")


def test_an_older_note_in_the_hosts_words_still_counts_as_stuck(cledger, host_sent):
    # record 2065 on the owner's box: written before the mark existed, in the host's words
    rec = cledger.get(host_sent[0].id)
    cledger.set_status(rec.id, S.SENT, expect=(S.SENT,), error=cr.stuck_note(4, 1, True))
    assert cr.is_stuck(cledger.get(rec.id))


def test_an_automatic_retry_is_not_stuck_until_the_next_pass_sees_error_again(cledger, host_sent, suwa, downloads,
                                                                            clock):
    host = HostFake(suwa)
    give_up(suwa)
    run(host, cledger, downloads)
    assert cledger.get(host_sent[1].id).stuck
    clock["t"] = T0 + timedelta(hours=7)
    run(host, cledger, downloads)                     # the automatic retry put it back in the queue
    got = cledger.get(host_sent[1].id)
    assert not got.stuck and got.error.startswith("MangaList retried it in the extension host (1 of 3)")
    assert cr.can_cancel(got)


def test_retry_and_cancel_go_to_the_records_own_program(db, cledger, host_sent, suwa, downloads, clock, monkeypatch):
    from mangalist.downloads.adapter import Backend

    give_up(suwa)
    run(HostFake(suwa), cledger, downloads)
    backend = Backend(db)                             # Suwayomi is chosen; the record is the host's
    asked = []

    def client(conn=None):
        asked.append(conn.backend)
        return HostFake(suwa)

    monkeypatch.setattr(backend, "_chapter_client", client)
    assert backend.retry_chapter_downloads([host_sent[1].id]) == 1
    assert asked == ["extensions"]


def test_the_notes_say_what_happens_next():
    assert cr.stuck_note(3, 0) == ("Suwayomi could not download it (3 tries); MangaList retries it within 6 h - or "
                                   "Retry / Cancel now")
    assert "retried it 2 of 3 times" in cr.stuck_note(5, 2)
    assert cr.stuck_note(6, 3) == "Suwayomi could not download it (6 tries; MangaList retried it 3 times) - Retry or Cancel"
