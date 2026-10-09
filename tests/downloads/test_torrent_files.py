"""The bencode reader: single- and multi-file torrents, nested paths, padding files, hashes, and malformed input."""

from __future__ import annotations

import hashlib

import pytest

from mangalist.torrent_files import MAX_FILES, TorrentError, decode, read_torrent

from .torrents import bencode, make_torrent


def test_decode_the_four_kinds():
    assert decode(b"i42e") == 42 and decode(b"i-7e") == -7 and decode(b"i0e") == 0
    assert decode(b"4:spam") == b"spam" and decode(b"0:") == b""
    assert decode(b"l4:spami3ee") == [b"spam", 3]
    assert decode(b"d3:bar4:spam3:fooi42ee") == {b"bar": b"spam", b"foo": 42}


@pytest.mark.parametrize("bad", [
    b"", b"i", b"ie", b"i-0e", b"i03e", b"i1.5e", b"i12", b"5:abc", b"l", b"li1e", b"d3:fooe", b"di1ei2ee", b"x",
    b"d3:fooi1e3:fooi2ee",                  # a repeated key
    b"i1ei2e",                              # extra data after the value
    b"i" + b"9" * 30 + b"e",                # an absurd number
])
def test_decode_refuses_malformed_input(bad):
    with pytest.raises(TorrentError):
        decode(bad)


def test_decode_is_bounded_in_depth():
    with pytest.raises(TorrentError, match="nested"):
        decode(b"l" * 200 + b"e" * 200)


def test_a_single_file_torrent():
    data = make_torrent("Series A v01 (Digital).cbz", length=5_000_000)
    listing = read_torrent(data)
    assert listing.name == "Series A v01 (Digital).cbz"
    assert [(f.name, f.size) for f in listing.files] == [("Series A v01 (Digital).cbz", 5_000_000)]
    assert listing.total_size == 5_000_000


def test_a_multi_file_torrent_names_files_the_way_qbittorrent_does():
    data = make_torrent("Series A v01-03", [("Series A v01.cbz", 10), ("Extras/cover.jpg", 5),
                                            ("Series A v02.cbz", 20)])
    listing = read_torrent(data)
    assert [(f.name, f.size) for f in listing.files] == [
        ("Series A v01-03/Series A v01.cbz", 10), ("Series A v01-03/Extras/cover.jpg", 5),
        ("Series A v01-03/Series A v02.cbz", 20)]
    assert listing.total_size == 35


def test_nested_paths_and_utf8_names():
    data = make_torrent("Pack", [("a/b/c/Série v01.cbz", 1)], extra_info={"name.utf-8": "Pack (utf-8)"})
    assert read_torrent(data).files[0].name == "Pack (utf-8)/a/b/c/Série v01.cbz"


def test_padding_files_are_not_files():
    info = {"name": "Pack", "piece length": 16384, "pieces": b"\0" * 20, "files": [
        {"length": 10, "path": ["a.cbz"]},
        {"length": 6, "path": [".pad", "6"], "attr": "p"},
        {"length": 10, "path": ["b.cbz"], "attr": "x"}]}
    listing = read_torrent(bencode({"info": info}))
    assert [f.name for f in listing.files] == ["Pack/a.cbz", "Pack/b.cbz"]


def test_the_info_hash_is_the_sha1_of_the_info_bytes_as_written():
    data = make_torrent("Pack", [("a.cbz", 1)])
    start = data.index(b"4:info") + len(b"4:info")
    info_bytes = data[start:-1]                                    # the info dictionary, then the outer 'e'
    listing = read_torrent(data)
    assert listing.info_hash == hashlib.sha1(info_bytes).hexdigest()
    assert listing.info_hash_v2 == hashlib.sha256(info_bytes).hexdigest()
    assert listing.has_hash(listing.info_hash.upper()) and listing.has_hash(listing.info_hash_v2)
    assert not listing.has_hash("0" * 40) and not listing.has_hash("")


def test_the_hash_does_not_depend_on_other_top_level_keys():
    one = read_torrent(make_torrent("Pack", [("a.cbz", 1)], announce="https://one.example/a"))
    two = read_torrent(make_torrent("Pack", [("a.cbz", 1)], announce="https://two.example/b"))
    assert one.info_hash == two.info_hash


@pytest.mark.parametrize("data, why", [
    (bencode({"announce": "x"}), "no info"),
    (bencode([1, 2]), "no info"),
    (bencode({"info": {"name": "p", "piece length": 1, "pieces": b""}}), "no files"),
    (bencode({"info": {"name": "p", "files": []}}), "no files"),
    (bencode({"info": {"name": "p", "files": [{"length": 1}]}}), "no path"),
    (bencode({"info": {"name": "p", "files": [{"length": -1, "path": ["a"]}]}}), "negative size"),
    (bencode({"info": {"name": "p", "files": [{"length": "7", "path": ["a"]}]}}), "size is a string"),
    (bencode({"info": {"name": "p", "length": -5}}), "negative size"),
    (bencode({"info": {"files": [{"length": 1, "path": ["a"]}]}}), "no name"),
    (bencode({"info": {"name": "p", "files": [{"length": 1, "path": ["..", "a"]}]}}), "path escapes"),
    (bencode({"info": {"name": "p", "files": [{"length": 1, "path": [""]}]}}), "empty path part"),
    (bencode({"info": {"name": "..", "length": 1}}), "unsafe name"),
    (bencode({"info": {"name": "p", "file tree": {"a": {"": {"length": 1}}}}}), "version 2 only"),
    (bencode({"info": {"name": "p", "files": ["not a dict"]}}), "file entry malformed"),
    (b"d4:infod4:name1:pee", "malformed"),
])
def test_unreadable_torrents_are_refused_with_a_reason(data, why):
    with pytest.raises(TorrentError) as err:
        read_torrent(data)
    assert str(err.value)


def test_path_separators_inside_a_name_do_not_make_new_folders():
    data = bencode({"info": {"name": "p", "files": [{"length": 1, "path": ["a/b\\c.cbz"]}]}})
    assert read_torrent(data).files[0].name == "p/a_b_c.cbz"


def test_a_torrent_with_too_many_files_is_refused():
    rows = [{"length": 1, "path": [f"f{i}.cbz"]} for i in range(MAX_FILES + 1)]
    with pytest.raises(TorrentError, match="more than"):
        read_torrent(bencode({"info": {"name": "p", "files": rows}}))


def test_something_that_is_not_bytes_or_is_huge_is_refused():
    with pytest.raises(TorrentError):
        read_torrent("not bytes")                                    # type: ignore[arg-type]
    with pytest.raises(TorrentError, match="large"):
        read_torrent(b"d" + b"0" * (17 * 1024 * 1024))
