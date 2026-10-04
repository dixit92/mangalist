"""Series identity: which archive is which after a move or rename, and which series it carries.

A port of MangaPixer's move / rename recognition, standalone (MangaPixer is an optional extra layer):

- :mod:`.signature` - MangaPixer's v1 content signature (byte-identical).
- :mod:`mangalist.store.archives` - one row per archive (root, series, path, size, mtime, signature, present /
  missing).
- :mod:`.backfill` - signs archives without a signature in the background (a signature must exist BEFORE a
  file moves).
- :mod:`.moves` - archive move detection (size -> signature, one-to-one only; duplicates and copies elsewhere
  are ambiguous) and the after-the-fact pairing.
- :mod:`.carry` - a vanished series' data follows >= 80% of its archives to one live folder, across all
  roots; never over that folder's own data; manual re-attach / forget.
- :mod:`.mangapixer` - MangaPixer's ``carriedFrom`` as an authoritative extra layer.

No Qt here (the headless runner and the no-Qt CI job import it).
"""

from __future__ import annotations

from .signature import byte_length, compute, compute_file, is_usable, signature_if_unchanged

__all__ = ["compute", "compute_file", "signature_if_unchanged", "byte_length", "is_usable"]
