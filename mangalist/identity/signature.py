"""MangaPixer's v1 content signature, computed by MangaList itself (byte-identical to MangaPixer's).

Port of MangaPixer ``ContentSignature`` (``src/MangaPixer.Core/Media/ContentSignature.cs``, 1.5.0):

    v1:<byteLength>:<sha256 hex>

The SHA-256 covers, in this order:

1. the byte length as a signed 64-bit **little-endian** integer (8 bytes - not its decimal text);
2. the first ``min(64 KiB, length)`` bytes (the head);
3. only when the file is longer than 64 KiB: the bytes from ``max(64 KiB, length - 64 KiB)`` to the end
   (the tail, without the part the head already covered).

At most 128 KiB is read per file, so it is cheap on a network share. Names and modification times are not
part of it: a rename or a move keeps it, any change of length, head or tail (an archive's central directory
sits at the tail) changes it. ``tests/identity/fixtures/signatures`` holds strings computed by MangaPixer's
own code for synthetic files (how: that folder's README).

A signature is only ever used to AVOID a remove + add pair: callers fall back to the safe default (removed +
added) when it is missing or ambiguous. No Qt here.
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import BinaryIO, Optional, Tuple

FORMAT_VERSION = "v1"
SAMPLE_BYTES = 64 * 1024
MAX_LENGTH = 96   # "v1:" + up to 20 digits + ":" + 64 hex


class FileChanged(OSError):
    """The file's size / mtime changed while (or before) it was read: no signature for it now."""


def bytes_read_for(length: int) -> int:
    """How many bytes :func:`compute` reads for a file of *length* bytes (at most 128 KiB)."""
    return max(0, min(int(length), 2 * SAMPLE_BYTES))


def _read_exactly(f: BinaryIO, n: int) -> bytes:
    chunks = []
    while n > 0:
        b = f.read(n)
        if not b:
            raise EOFError("File shrank while computing its content signature.")
        chunks.append(b)
        n -= len(b)
    return b"".join(chunks)


def compute(f: BinaryIO, length: Optional[int] = None) -> str:
    """The signature of a seekable binary stream of *length* bytes (default: its size by seeking to the
    end). The stream position is not preserved."""
    if length is None:
        length = f.seek(0, os.SEEK_END)
    length = int(length)
    h = hashlib.sha256(struct.pack("<q", length))
    f.seek(0)
    head = min(SAMPLE_BYTES, length)
    h.update(_read_exactly(f, head))
    if length > SAMPLE_BYTES:
        tail_start = max(SAMPLE_BYTES, length - SAMPLE_BYTES)
        f.seek(tail_start)
        h.update(_read_exactly(f, length - tail_start))
    return f"{FORMAT_VERSION}:{length}:{h.hexdigest()}"


def compute_file(path) -> Optional[str]:
    """The signature of the file at *path*, or None when it cannot be read (never raises for I/O)."""
    try:
        with open(path, "rb") as f:
            return compute(f, os.fstat(f.fileno()).st_size)
    except (OSError, EOFError):
        return None


def stamp(path) -> Tuple[int, int]:
    """``(size, mtime_ns)`` of *path* (raises OSError)."""
    st = os.stat(path)
    return int(st.st_size), int(st.st_mtime_ns)


def signature_if_unchanged(path, size: int, mtime_ns: int) -> Optional[str]:
    """The signature of *path* when its stamp is ``(size, mtime_ns)`` before AND after hashing, else None
    (a file still being written, or changed since it was seen, is left for later - MangaPixer's
    ``ContentSignatureBackfill.SignatureOf`` / ``ComputeObservedSignature``)."""
    try:
        before = stamp(path)
        if before != (int(size), int(mtime_ns)):
            return None
        with open(path, "rb") as f:
            sig = compute(f, before[0])
        if stamp(path) != before:
            return None
    except (OSError, EOFError):
        return None
    return sig if byte_length(sig) == int(size) else None


def byte_length(signature: Optional[str]) -> Optional[int]:
    """The byte length inside a well-formed v1 signature, else None (``TryGetByteLength``)."""
    if not signature:
        return None
    parts = str(signature).split(":")
    if len(parts) != 3 or parts[0] != FORMAT_VERSION or not parts[1].isdigit() or not parts[1].isascii():
        return None
    return int(parts[1])


def is_usable(signature: Optional[str], size: Optional[int]) -> bool:
    """A stored signature that may be used: present and describing the stored length
    (``MoveEvidence.IsUsable``)."""
    return size is not None and byte_length(signature) == int(size)


__all__ = ["FORMAT_VERSION", "SAMPLE_BYTES", "MAX_LENGTH", "FileChanged", "bytes_read_for", "compute", "compute_file",
           "stamp", "signature_if_unchanged", "byte_length", "is_usable"]
