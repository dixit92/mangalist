"""``NyaaSearch``: :class:`mangalist.downloads.contracts.VolumeSearch` over nyaa's RSS.

For a series known by several names: always query the first two names and merge the results, the further
alternatives only while nothing usable has been found (at most ``max_queries`` requests, 2 s apart); keep the
releases that really are the series (see :mod:`.ranking`), drop 0-seeder results, hide light novels / EPUBs (unless
asked), drop chapter releases and releases that certainly fill none of the missing volumes (with no missing list:
that hold only volumes already held), dedupe by info hash, rank best first.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Dict, List, Optional, Sequence

from ...downloads.contracts import NyaaCandidate
from ...matcher.normalizer import normalize, scoring_form
from .client import CATEGORY_ENGLISH_TRANSLATED, NyaaClient
from .ranking import build_candidate, fills_nothing, matches_series, order, series_forms
from .titles import parse_title

_log = logging.getLogger(__name__)

DEFAULT_MAX_QUERIES = 4
#: The first names are always asked (releases are named in different languages); later ones only while nothing
#: usable has been found.
ALWAYS_ASKED = 2
_QUERY_JUNK = re.compile(r"[^\w']+", re.UNICODE)


def query_text(title: str) -> str:
    """The search text for a series name: edition words and brackets dropped, punctuation turned into spaces."""
    base = normalize(title).primary or title
    text = _QUERY_JUNK.sub(" ", unicodedata.normalize("NFKC", base))
    return re.sub(r"\s+", " ", text).strip()


class NyaaSearch:
    """English-translated literature by default (``3_1``); the category is a parameter (raws are ``3_3``)."""

    def __init__(self, client: Optional[NyaaClient] = None, *, category: str = CATEGORY_ENGLISH_TRANSLATED,
                 include_not_comic: bool = False, keep_no_coverage: bool = False,
                 max_queries: int = DEFAULT_MAX_QUERIES):
        self.client = client or NyaaClient()
        self.category = category
        self.include_not_comic = include_not_comic
        self.keep_no_coverage = keep_no_coverage
        self.max_queries = max(1, int(max_queries))

    def search(self, titles: Sequence[str], missing: Sequence[str], held: Sequence[str]) -> List[NyaaCandidate]:
        names = [t for t in titles if t and t.strip()]
        if not names:
            return []
        forms = series_forms(names)
        found: Dict[str, NyaaCandidate] = {}
        asked = set()
        for name in names:
            query = query_text(name)
            key = scoring_form(query).replace(" ", "")
            if not key or key in asked:
                continue
            if len(asked) >= self.max_queries:
                break
            asked.add(key)
            for item in self.client.rss(query, self.category):
                if item.info_hash in found:
                    continue
                parsed = parse_title(item.title)
                if parsed.chapters_only or item.seeders <= 0 or not matches_series(parsed, forms):
                    continue
                if parsed.not_comic and not self.include_not_comic:
                    continue
                candidate = build_candidate(item, parsed, missing, held)
                if not self.keep_no_coverage and fills_nothing(candidate, parsed, missing):
                    continue
                found[item.info_hash] = candidate
            if found and len(asked) >= ALWAYS_ASKED:
                break
            _log.info("nyaa: nothing usable for %r; trying the next title", query)
        return order(found.values())


__all__ = ["DEFAULT_MAX_QUERIES", "NyaaSearch", "query_text"]
