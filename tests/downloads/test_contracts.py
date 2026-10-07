from mangalist.downloads.contracts import (
    QBITTORRENT_CATEGORY, DownloadStatus, NyaaCandidate, Placement, QbtConnection, TorrentInfo, downloads_enabled,
)


def test_downloads_switch():
    assert downloads_enabled({}) is False
    assert downloads_enabled({"MANGALIST_DOWNLOADS": "0"}) is False
    assert downloads_enabled({"MANGALIST_DOWNLOADS": " Yes "}) is True


def test_category_and_statuses():
    assert QBITTORRENT_CATEGORY == "mangalist"
    assert len(set(DownloadStatus.ALL)) == 6


def _torrent(state, progress=1.0):
    return TorrentInfo("ab" * 20, "x", "mangalist", state, progress, "/data/t", "/data/t/x", 2.0, 60)


def test_torrent_states():
    assert _torrent("stoppedUP").stopped_complete and _torrent("stoppedUP").complete
    assert _torrent("pausedUP").stopped_complete
    assert not _torrent("uploading").stopped_complete
    assert not _torrent("stoppedDL", 0.5).stopped_complete


def test_magnet_and_placement():
    c = NyaaCandidate("t", "v", "u", "ab" * 20, 1, 1, 0, 0, False, False, "2026-10-01T00:00:00Z", "3_1")
    assert c.magnet == "magnet:?xt=urn:btih:" + "ab" * 20
    assert Placement("/s", None, "mixed", ("/s/a", "/s/b")).ambiguous
    assert not Placement("/s", "/s", "flat").ambiguous


def test_password_not_in_repr():
    assert "hunter2" not in repr(QbtConnection("http://h:8080", "u", "hunter2"))
