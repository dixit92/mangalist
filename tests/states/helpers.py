"""Synthetic MangaPixer export items and knowledge for the state tests (made-up names and ids, the
documented v1 item shape)."""

from __future__ import annotations

import copy
import datetime as dt

from mangalist.knowledge import SeriesKnowledge

TODAY = dt.date(2026, 10, 3)

_ITEM = {
    "nodeId": "n1",
    "nodeKind": "folder",
    "carriedFrom": None,
    "trail": ["Shelf", "Example Quest"],
    "updatedAt": "2026-10-03T12:00:00.000Z",
    "link": {"state": "Confirmed", "method": "search", "score": 0.97, "updatedAt": "2026-09-28T12:00:00.000Z"},
    "record": {
        "provider": "mangaupdates",
        "externalId": "90000000001",
        "siteUrl": "https://www.mangaupdates.com/series/example/example-quest",
        "title": "Example Quest",
        "altTitles": ["Example Quest: Second Name"],
        "type": "Manga",
        "originStatus": "Ongoing",
        "originVolumes": 5,
        "latestChapter": "41",
        "totalChapters": None,
        "statusText": "5 Volumes (Ongoing)",
        "licensedEn": True,
        "translationComplete": False,
        "completedInOrigin": False,
        "englishPublishers": [
            {"name": "Example Press", "volumes": 3, "chapters": None, "status": "Ongoing", "omnibus": False}
        ],
        "fetchedAt": "2026-09-30T12:00:00.000Z",
        "someFutureField": {"ignored": True},
    },
    "companions": {"mangadex": "00000000-0000-4000-8000-00000000000a",
                   "anilist": {"id": 900001, "chapters": 41, "volumes": 5}},
    "officialLinks": [
        {"kind": "publisher", "label": "Official (original language)",
         "url": "https://publisher.example.com/example-quest", "source": "mangadex"},
        {"kind": "store", "label": "BookWalker", "url": "https://store.example.com/series/1", "source": "mangadex"},
        {"kind": "publisher", "label": "Official English release",
         "url": "https://english.example.com/titles/example-quest", "source": "mangadex"},
    ],
    "volumes": {"source": "merged", "fetchedAt": "2026-10-02T12:00:00.000Z", "items": [
        {"volume": "1", "title": None, "chapters": {"from": "1", "to": "8"}, "englishDate": "2025-03-04",
         "englishDateKind": "released", "isbn": "9780000000011", "sources": ["mangadex"]},
        {"volume": "2", "title": None, "chapters": {"from": "9", "to": "16"}, "englishDate": "2025-07",
         "englishDateKind": "released", "isbn": None, "sources": ["wikipedia"]},
        {"volume": "3", "title": None, "chapters": {"from": "17", "to": "24.5"}, "englishDate": "2026",
         "englishDateKind": "released", "isbn": None, "sources": ["wikipedia"]},
        {"volume": "4", "title": None, "chapters": None, "englishDate": "2027-02-09",
         "englishDateKind": "announced", "isbn": None, "sources": ["wikipedia"]},
    ]},
    "completion": {"answer": "MissingSome", "reason": "Running", "upgradeAvailable": False, "upgradeVolumes": [],
                   "computedAt": "2026-10-03T12:00:00.000Z", "basedOnScanAt": "2026-10-03T11:00:00.000Z"},
    "refresh": {"lastFetchedAt": "2026-09-30T12:00:00.000Z", "nextDueAt": "2026-10-14T12:00:00.000Z",
                "intervalDays": 14},
}


def item(**changes) -> dict:
    """A copy of the synthetic item; ``record__x=...`` changes record field x, other keys the item."""
    it = copy.deepcopy(_ITEM)
    for key, value in changes.items():
        if key.startswith("record__"):
            it["record"][key[len("record__"):]] = value
        else:
            it[key] = value
    return it


def vol(volume, frm=None, to=None, date=None, kind=None):
    return {"volume": volume, "title": None, "chapters": {"from": frm, "to": to} if frm is not None else None,
            "englishDate": date, "englishDateKind": kind, "isbn": None, "sources": []}


def own(**fields) -> SeriesKnowledge:
    """Own-matcher knowledge, linked (Auto) unless *link_state* says otherwise."""
    base = dict(source="own-matcher", link_state="Auto", mu_id="1", title="Example Saga")
    base.update(fields)
    return SeriesKnowledge(**base)
