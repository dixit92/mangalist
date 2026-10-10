from mangalist.downloads.contracts import (
    QBITTORRENT_CATEGORY, DownloadStatus, NyaaCandidate, Placement, QbtConnection, TorrentInfo, downloads_enabled,
)


def test_downloads_switch():
    assert downloads_enabled({}) is False
    assert downloads_enabled({"MANGALIST_DOWNLOADS": "0"}) is False
    assert downloads_enabled({"MANGALIST_DOWNLOADS": " Yes "}) is True


def test_category_and_statuses():
    assert QBITTORRENT_CATEGORY == "mangalist"
    assert len(set(DownloadStatus.ALL)) == 7 and DownloadStatus.QUEUED == "queued"


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


def test_seed_goal_reached():
    def t(ratio, seconds, max_ratio, max_minutes):
        return TorrentInfo("ab" * 20, "x", "mangalist", "stoppedUP", 1.0, "/d", "/d/x", ratio, seconds,
                           max_ratio=max_ratio, max_seeding_time=max_minutes)
    assert t(2.0, 0, 2.0, -1).seed_goal_reached and t(1.999, 0, 2.0, -1).seed_goal_reached     # qBittorrent's rounding
    assert not t(1.5, 0, 2.0, -1).seed_goal_reached
    assert t(0.1, 87600 * 60, 2.0, 87600).seed_goal_reached and not t(0.1, 600, 2.0, 87600).seed_goal_reached
    assert not t(5.0, 10 ** 9, -1.0, -1).seed_goal_reached and not t(5.0, 10 ** 9, None, None).seed_goal_reached
