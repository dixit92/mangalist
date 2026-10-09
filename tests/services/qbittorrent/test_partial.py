"""The qBittorrent client's calls for a partial download: a stopped add, file ids and priorities, start / stop under
both generations of names, and the 5.x answers."""

from __future__ import annotations

import pytest

from mangalist.downloads.contracts import STOPPED_DOWNLOADING_STATES
from mangalist.services.qbittorrent import TorrentNotFound, UnexpectedResponse

from .conftest import HASH_A, HASH_B

LINK = "https://nyaa.example/download/1.torrent"


def rows(n=4):
    return [{"name": f"Pack/Series A v{i:02d}.cbz", "size": 1000 * i, "progress": 0.0, "priority": 1, "index": i - 1,
             "is_seed": False} for i in range(1, n + 1)]


def test_a_stopped_add_sends_both_generations_of_the_flag(client, qbt):
    client.add(LINK, category="mangalist", stopped=True)
    form = qbt.add_forms[0]
    assert form["stopped"] == "true" and form["paused"] == "true"                 # 5.x name, 4.x name
    assert form["category"] == "mangalist" and form["autoTMM"] == "true" and form["urls"] == LINK
    client.add(LINK + "2", category="mangalist")
    assert "stopped" not in qbt.add_forms[1] and "paused" not in qbt.add_forms[1]


def test_a_stopped_add_under_qbittorrent_5_answers_accepted(client, qbt):
    qbt.v5 = True
    client.add(LINK, category="mangalist", stopped=True)                           # 202 is a success
    assert qbt.added == [(LINK, "mangalist")]


def test_files_carry_the_file_id_and_priority(client, qbt):
    qbt.put_torrent(HASH_A)
    qbt.files[HASH_A] = rows(3)
    qbt.files[HASH_A][1]["priority"] = 0
    files = client.files(HASH_A)
    assert [(f.index, f.priority) for f in files] == [(0, 1), (1, 0), (2, 1)]
    assert files[0].name == "Pack/Series A v01.cbz" and files[0].size == 1000


def test_files_without_ids_fall_back_to_the_list_position(client, qbt):
    qbt.put_torrent(HASH_A)
    qbt.files[HASH_A] = [{"name": "a.cbz", "size": 1, "progress": 0.0}, {"name": "b.cbz", "size": 2, "progress": 1.0}]
    assert [(f.index, f.priority) for f in client.files(HASH_A)] == [(0, 1), (1, 1)]


def test_set_file_priority_sends_the_ids_pipe_separated(client, qbt):
    qbt.put_torrent(HASH_A)
    qbt.files[HASH_A] = rows(4)
    client.set_file_priority(HASH_A, [3, 0, 3], 0)
    request = qbt.calls("torrents/filePrio")[0]
    assert request["method"] == "POST" and request["form"] == {"hash": HASH_A, "id": "0|3", "priority": "0"}
    assert [r["priority"] for r in qbt.files[HASH_A]] == [0, 1, 1, 0]
    client.set_file_priority(HASH_A, [0], 1)
    assert qbt.files[HASH_A][0]["priority"] == 1


def test_set_file_priority_refuses_nonsense_before_sending(client, qbt):
    for ids, prio in (([], 0), ([-1], 0), ([0], 3), ([0], -1)):
        with pytest.raises(ValueError):
            client.set_file_priority(HASH_A, ids, prio)
    with pytest.raises(ValueError):
        client.set_file_priority("not a hash", [0], 0)
    assert qbt.calls("torrents/filePrio") == []


def test_set_file_priority_answers(client, qbt):
    with pytest.raises(TorrentNotFound):
        client.set_file_priority(HASH_B, [0], 0)                                  # 404: no such torrent
    qbt.put_torrent(HASH_A)
    with pytest.raises(UnexpectedResponse, match="no file list"):
        client.set_file_priority(HASH_A, [0], 0)                                  # 409: no metadata yet
    qbt.files[HASH_A] = rows(2)
    with pytest.raises(UnexpectedResponse, match="no file list|no such file"):
        client.set_file_priority(HASH_A, [9], 0)                                  # 409: unknown file id
    qbt.fail_with["torrents/filePrio"] = 500
    with pytest.raises(UnexpectedResponse, match="server error"):
        client.set_file_priority(HASH_A, [0], 0)


def test_start_and_stop_use_the_5x_names_first(client, qbt):
    qbt.v5 = True
    qbt.put_torrent(HASH_A, state="stoppedDL", progress=0.0)
    client.start(HASH_A)
    assert qbt.actions == [("start", HASH_A)] and qbt.torrents[HASH_A]["state"] == "downloading"
    client.stop(HASH_A)
    assert qbt.actions[-1] == ("stop", HASH_A) and qbt.torrents[HASH_A]["state"] in STOPPED_DOWNLOADING_STATES


def test_start_and_stop_fall_back_to_the_4x_names(client, qbt):
    qbt.put_torrent(HASH_A, state="pausedDL", progress=0.0)
    client.start(HASH_A)
    client.stop(HASH_A)
    assert [a[0] for a in qbt.actions] == ["resume", "pause"]                     # 'start' / 'stop' answered 404 first
    assert [r["path"] for r in qbt.requests if r["path"].startswith("torrents/") and "files" not in r["path"]] == [
        "torrents/start", "torrents/resume", "torrents/stop", "torrents/pause"]
    assert qbt.torrents[HASH_A]["state"] == "pausedDL"


def test_start_takes_one_well_formed_hash(client, qbt):
    for bad in ("all", "", HASH_A + "|" + HASH_B, "xyz"):
        with pytest.raises(ValueError):
            client.start(bad)
        with pytest.raises(ValueError):
            client.stop(bad)
    assert qbt.actions == []


def test_a_server_error_on_start_is_an_error(client, qbt):
    qbt.v5 = True
    qbt.fail_with["torrents/start"] = 500
    with pytest.raises(UnexpectedResponse):
        client.start(HASH_A)


def test_the_client_satisfies_the_contract_for_partial_downloads(client):
    for name in ("set_file_priority", "start", "stop", "add", "files"):
        assert callable(getattr(client, name))
