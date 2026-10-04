"""(j) The content signature is byte-identical to MangaPixer's (fixture computed by MangaPixer's own code)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from mangalist.identity import signature as sig

FIXTURE = Path(__file__).parent / "fixtures" / "signatures" / "expected.tsv"

# The synthetic files the fixture was computed from (see fixtures/signatures/README.md).
SIZES = {"empty": 0, "one-byte": 1, "small-1000": 1000, "head-minus-1": 65535, "exactly-64k": 65536,
         "head-plus-1": 65537, "between-100000": 100000, "128k-minus-1": 131071, "exactly-128k": 131072,
         "128k-plus-1": 131073, "large-200000": 200000, "large-1mib-plus-7": 1048583}


def synthetic_bytes(name: str, size: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < size:
        out += hashlib.sha256(f"{name}:{i}".encode()).digest()
        i += 1
    return bytes(out[:size])


def _expected():
    rows = {}
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, value = line.split("\t")
            rows[name] = value.strip()
    return rows


def test_fixture_covers_every_size():
    assert set(_expected()) == {f"{n}.bin" for n in SIZES}


@pytest.mark.parametrize("name", sorted(SIZES))
def test_signature_is_byte_identical_to_mangapixer(tmp_path, name):
    path = tmp_path / f"{name}.bin"
    path.write_bytes(synthetic_bytes(name, SIZES[name]))
    expected = _expected()[f"{name}.bin"]
    assert sig.compute_file(path) == expected
    st = path.stat()
    assert sig.signature_if_unchanged(path, st.st_size, st.st_mtime_ns) == expected
    with open(path, "rb") as f:
        assert sig.compute(f) == expected


def test_layout_length_is_int64_little_endian_then_head_then_tail():
    data = synthetic_bytes("layout", 300_000)
    h = hashlib.sha256((300_000).to_bytes(8, "little", signed=True))
    h.update(data[:65536])
    h.update(data[300_000 - 65536:])
    assert sig.compute(io.BytesIO(data)) == f"v1:300000:{h.hexdigest()}"
    # Between 64 and 128 KiB the tail starts right after the head (nothing hashed twice).
    small = synthetic_bytes("layout", 100_000)
    h = hashlib.sha256((100_000).to_bytes(8, "little", signed=True))
    h.update(small[:65536])
    h.update(small[65536:])
    assert sig.compute(io.BytesIO(small)) == f"v1:100000:{h.hexdigest()}"


def test_name_and_mtime_are_not_part_of_it_but_any_byte_at_either_end_is(tmp_path):
    a = tmp_path / "a.cbz"
    a.write_bytes(synthetic_bytes("x", 200_000))
    b = tmp_path / "renamed" / "other name.cbz"
    b.parent.mkdir()
    b.write_bytes(a.read_bytes())
    assert sig.compute_file(a) == sig.compute_file(b)
    tail = bytearray(a.read_bytes())
    tail[-1] ^= 1
    b.write_bytes(bytes(tail))
    assert sig.compute_file(a) != sig.compute_file(b)
    middle = bytearray(a.read_bytes())
    middle[100_000] ^= 1          # outside both samples: by design not seen
    b.write_bytes(bytes(middle))
    assert sig.compute_file(a) == sig.compute_file(b)


def test_helpers(tmp_path):
    assert sig.byte_length("v1:123:abc") == 123
    assert sig.byte_length("v2:123:abc") is None and sig.byte_length("v1:x:abc") is None
    assert sig.byte_length(None) is None and sig.byte_length("garbage") is None
    assert sig.is_usable("v1:10:ab", 10) and not sig.is_usable("v1:10:ab", 11) and not sig.is_usable(None, 10)
    assert sig.bytes_read_for(0) == 0 and sig.bytes_read_for(1000) == 1000 and sig.bytes_read_for(10**9) == 131072
    assert sig.compute_file(tmp_path / "nope.cbz") is None


def test_signature_if_unchanged_refuses_a_changed_stamp(tmp_path):
    p = tmp_path / "a.cbz"
    p.write_bytes(b"abc")
    st = p.stat()
    assert sig.signature_if_unchanged(p, st.st_size + 1, st.st_mtime_ns) is None
    assert sig.signature_if_unchanged(p, st.st_size, st.st_mtime_ns + 1) is None
    assert sig.signature_if_unchanged(tmp_path / "gone.cbz", 3, 0) is None


def test_journal_hash_mode_uses_the_same_signature(tmp_path):
    from mangalist.store.journal import content_signature

    p = tmp_path / "a.cbz"
    p.write_bytes(synthetic_bytes("exactly-128k", 131072))
    assert content_signature(str(p), "hash") == _expected()["exactly-128k.bin"]
