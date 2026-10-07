"""send_pick: the owner's pick goes to qBittorrent's ``mangalist`` category and becomes a SENT record."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import DownloadStatus, Placement
from mangalist.downloads.service import SendRefused, send_pick

from .fakes import HASH, candidate


@pytest.fixture
def place(series):
    _, sdir = series
    return Placement(str(sdir), str(sdir), "volumes live in the series folder")


def test_send_pick(ledger, series, qbt, place, tmp_path):
    sid, sdir = series
    save = str(tmp_path / "torrents" / "mangalist")
    rec = send_pick(qbt, ledger, sid, candidate(), ["02", "3"], place, save)
    assert qbt.calls == [("ensure_category", "mangalist", save),
                         ("add", "https://nyaa.example/download/1.torrent", "mangalist")]
    assert rec.status == DownloadStatus.SENT and rec.wanted_volumes == ("2", "3") and rec.target_dir == str(sdir)
    assert ledger.active() == [rec]


def test_a_magnet_when_there_is_no_torrent_url(ledger, series, qbt, place, tmp_path):
    send_pick(qbt, ledger, series[0], candidate(torrent_url=""), ["2"], place, str(tmp_path / "t"))
    assert qbt.calls[-1] == ("add", f"magnet:?xt=urn:btih:{HASH}", "mangalist")


def test_refusals_add_nothing(ledger, series, qbt, place, tmp_path, library):
    sid, sdir = series
    save = str(tmp_path / "t")
    ambiguous = Placement(str(sdir), None, "volumes are spread over 2 folders; choose one", (str(sdir),))
    with pytest.raises(SendRefused, match="not decided"):
        send_pick(qbt, ledger, sid, candidate(), ["2"], ambiguous, save)
    outside = Placement(str(sdir), str(tmp_path), "x")
    with pytest.raises(SendRefused, match="inside it"):
        send_pick(qbt, ledger, sid, candidate(), ["2"], outside, save)
    with pytest.raises(SendRefused, match="at least one"):
        send_pick(qbt, ledger, sid, candidate(), [], place, save)
    with pytest.raises(SendRefused, match="overlaps the library root"):
        send_pick(qbt, ledger, sid, candidate(), ["2"], place, str(library / "_downloads"))
    with pytest.raises(SendRefused, match="absolute"):
        send_pick(qbt, ledger, sid, candidate(), ["2"], place, "relative/path")
    assert qbt.calls == [] and ledger.active() == []

    send_pick(qbt, ledger, sid, candidate(), ["2"], place, save)
    with pytest.raises(SendRefused, match="already being downloaded"):
        send_pick(qbt, ledger, sid, candidate(), ["3"], place, save)
    assert len(qbt.calls) == 2
