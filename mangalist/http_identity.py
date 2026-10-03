"""The identity MangaList sends to the web services it reads (MangaUpdates, AniList).

A fixed, descriptive User-Agent - the program, its version and its project page - is the same for every
installation of a version, so it identifies the program, not the user. AniList's Cloudflare front blocks
generic client signatures (HTTP 403, error 1010), so a library's default User-Agent is not enough.
"""

from __future__ import annotations

from ._version import __version__

PROJECT_URL = "https://github.com/dixit92/MangaList"
USER_AGENT = f"MangaList/{__version__} (+{PROJECT_URL})"
