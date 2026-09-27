"""The stage-2 golden set (port of MangaPixer 1.26.0 ``GoldenCases.cs``).

One case = a synthetic folder (PUBLIC, well-known titles rendered as folder / archive names - never
anything from a real library) and the expected detector class, band and chosen MangaUpdates id.
``group_title`` selects an archive group (archive-level cases); ``band`` None makes it a
detector-only case. ``vetoes``, when set, is the exact set of auto-vetoing reasons of the top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from manga_list.matcher import (
    ChildFolderShape,
    ContentSuggestion,
    FolderShape,
    MatchBand,
    MatchReason,
    WorkClass,
)


@dataclass(frozen=True)
class GoldenCase:
    id: str
    folder: FolderShape
    cls: Optional[WorkClass] = None
    band: Optional[MatchBand] = None
    expected_id: Optional[str] = None
    comic_info: Optional[str] = None
    doujin_allowed: bool = False
    group_title: Optional[str] = None
    content: Optional[ContentSuggestion] = None
    vetoes: Optional[MatchReason] = None

    def __str__(self) -> str:
        return self.id


def F(name: str, archives: Iterable[str], category: Optional[str] = None, parent: Optional[str] = None,
      subs: Sequence[Tuple[str, int]] = (), depth: int = 2) -> FolderShape:
    return FolderShape(name, depth, tuple(archives), tuple(ChildFolderShape(n, c) for n, c in subs), parent, category)


def Vols(title: str, n: int, suffix: str = "") -> list:
    return [f"{title} v{i:02d}{suffix}.cbz" for i in range(1, n + 1)]


def Chaps(title: str, n: int) -> list:
    return [f"{title} - Chapter {i:03d}.cbz" for i in range(1, n + 1)]


def Units(n: int) -> list:
    return [f"{i:03d} [Chapter Title {i}].cbz" for i in range(1, n + 1)]


BERSERK = "51239621230"
DUNGEON_MESHI = "19088665446"
ATTACK_ON_TITAN = "23393951235"
SOLO_LEVELING = "15180124327"
VINLAND_SAGA = "27728982867"
ONE_PIECE = "55099564912"
CHAINSAW_MAN = "75336092483"
LOOK_BACK = "62512335978"
SAYONARA_ERI = "47603342373"
JOJO_PART3 = "60420553585"
JOJO_PART4 = "42553317740"
TOKYO_GHOUL = "26272522291"
MOB_PSYCHO = "605012986"
TWENTIETH_CENTURY_BOYS = "45334600346"
TWENTY_FIRST_CENTURY_BOYS = "64832756793"
YOTSUBA = "23606352927"
HUNTER_X_HUNTER = "49449837876"
JIGOKURAKU_KAKU = "61508275290"
FRIEREN = "66296374554"
SPY_X_FAMILY = "67814124606"
TOWER_OF_GOD = "13015731700"
OMNISCIENT_READER = "50369844984"
MONSTER_URASAWA = "72274276213"
AKIRA = "46397795369"
PUNPUN = "21944750964"
FIRE_PUNCH = "32334361267"
YOTSUBA_DJ_YANDA = "57918701059"

ARTIST_FOLDER = [
    "[Fujimoto Tatsuki] Look Back (2021) (Digital).cbz",
    "[Fujimoto Tatsuki] Sayonara Eri (2022) (Digital).cbz",
    "[Fujimoto Tatsuki] Fire Punch v01.cbz",
    "[Fujimoto Tatsuki] Fire Punch v02.cbz",
    "[Fujimoto Tatsuki] Fire Punch v03.cbz",
    "[Fujimoto Tatsuki] Berserk v01.cbz",
]

COLLECTION_LEAF = [
    "Look Back.cbz",
    "Sayonara Eri.cbz",
    "Hunter x Hunter v01.cbz",
    "Akira v01.cbz",
    "Oyasumi Punpun v01.cbz",
]

# Doujin naming anatomy; the artist is the provider-listed author of the recorded dj record.
DOUJIN_SHELF = [
    "(Comic Event 72) [Circle Placeholder (Hideyoshico)] Yanda&! 1 (Yotsuba).cbz",
    "(Comic Event 73) [Circle Placeholder (Hideyoshico)] Yanda&! 2 (Yotsuba).cbz",
    "(Comic Event 74) [Circle Placeholder (Hideyoshico)] Yanda&! 3 (Yotsuba).cbz",
    "(Comic Event 80) [Other Circle (Other Artist)] Another Story (Some Parody).cbz",
    "[Third Circle (Third Artist)] A Third Story (Other Parody).cbz",
    "[Fourth Circle (Fourth Artist)] A Fourth Story (Other Parody).cbz",
    "(Comic Event 81) [Fifth Circle (Fifth Artist)] A Fifth Story (Some Parody).cbz",
]

DOUJIN_SHELF_LONE = [
    "(Comic Event 72) [Circle Placeholder (Hideyoshico)] Yanda&! (Yotsuba).cbz",
    "(Comic Event 80) [Other Circle (Other Artist)] Another Story (Some Parody).cbz",
    "[Third Circle (Third Artist)] A Third Story (Other Parody).cbz",
]

S, SWU, ONE = WorkClass.SERIES, WorkClass.SERIES_WITH_UNITS, WorkClass.ONE_SHOT
AUTO, REVIEW, UNMATCHED = MatchBand.AUTO, MatchBand.NEEDS_REVIEW, MatchBand.UNMATCHED
G = GoldenCase

ALL: Tuple[GoldenCase, ...] = (
    # --- Folder level: romaji / English / scene-style names -----------------------------------
    G("F01 romaji, digital volumes", F("Berserk", Vols("Berserk", 41, " (Digital)"), "Manga", "Manga"), S, AUTO, BERSERK),
    G("F02 romaji + [English], English archive names", F("Dungeon Meshi [Delicious in Dungeon]", Vols("Delicious in Dungeon", 14, " (2017) (Digital)")), S, AUTO, DUNGEON_MESHI),
    G("F03 English folder name finds the romaji record", F("Delicious in Dungeon", Vols("Delicious in Dungeon", 14)), S, AUTO, DUNGEON_MESHI),
    G("F04 chapter archives", F("Dungeon Meshi", Chaps("Dungeon Meshi", 97)), S, AUTO, DUNGEON_MESHI),
    G("F05 romaji, chapters (novel twins filtered)", F("Shingeki no Kyojin", Chaps("Shingeki no Kyojin", 139)), S, AUTO, ATTACK_ON_TITAN),
    G("F06 English name, volumes", F("Attack on Titan", Vols("Attack on Titan", 34)), S, AUTO, ATTACK_ON_TITAN),
    G("F07 manhwa under a Manhwa category", F("Solo Leveling", Units(200), "Manhwa"), S, AUTO, SOLO_LEVELING),
    G("F08 manhwa under a Manga category: origin conflict -> review", F("Solo Leveling", Units(200), "Manga"), S, REVIEW, SOLO_LEVELING, vetoes=MatchReason.TYPE_CONFLICT),
    G("F09 scene-style archive names", F("Vinland Saga", Vols("Vinland Saga", 12, " (2013) (Digital) (Scan Team)")), S, AUTO, VINLAND_SAGA),
    G("F10 meaningless folder name, ComicInfo series", F("Unsorted Batch", Vols("Vinland Saga", 5)), S, AUTO, VINLAND_SAGA, comic_info="Vinland Saga"),
    G("F11 long-running chapters", F("One Piece", Chaps("One Piece", 1100)), S, AUTO, ONE_PIECE),
    G("F12 volumes", F("Chainsaw Man", Vols("Chainsaw Man", 20)), S, AUTO, CHAINSAW_MAN),
    G("F13 one-shot folder, disambiguated provider title", F("Look Back", ["Look Back (2021) (Digital).cbz"]), ONE, AUTO, LOOK_BACK),
    G("F14 one-shot title with ten volume archives: count conflict", F("Look Back", Vols("Look Back", 10)), S, REVIEW, LOOK_BACK, vetoes=MatchReason.COUNT_CONFLICT),
    G("F15 one-shot folder, romaji", F("Sayonara Eri", ["Sayonara Eri.cbz"]), ONE, AUTO, SAYONARA_ERI),
    G("F16 one-shot folder, English", F("Goodbye, Eri", ["Goodbye, Eri.cbz"]), ONE, AUTO, SAYONARA_ERI),
    G("F17 numbered part with subtitle", F("JoJo no Kimyou na Bouken Part 3 - Stardust Crusaders", Vols("JoJo no Kimyou na Bouken Part 3", 16), depth=3), S, AUTO, JOJO_PART3),
    G("F18 numbered part, the provider ranks another part first", F("JoJo no Kimyou na Bouken Part 4 - Diamond wa Kudakenai", Vols("JoJo no Kimyou na Bouken Part 4", 18), depth=3), S, AUTO, JOJO_PART4),
    G("F19 short romaji; official doujin + subtitled main record", F("Kaguya-sama wa Kokurasetai", Vols("Kaguya-sama wa Kokurasetai", 28)), S, REVIEW),
    G("F20 main record absent from the search results", F("Re Zero kara Hajimeru Isekai Seikatsu", Chaps("Re Zero kara Hajimeru Isekai Seikatsu", 50)), S, REVIEW),
    G("F21 sequel exists (:re)", F("Tokyo Ghoul", Vols("Tokyo Ghoul", 14)), S, AUTO, TOKYO_GHOUL),
    G("F22 sequel folder whose record the search misses (the prequel must not count as right)", F("Tokyo Ghoul re", Vols("Tokyo Ghoul re", 16)), S, REVIEW),
    G("F23 number that is part of the name", F("Mob Psycho 100", Vols("Mob Psycho 100", 16)), S, AUTO, MOB_PSYCHO),
    G("F24 leading number, English alt title", F("20th Century Boys", Vols("20th Century Boys", 22)), S, AUTO, TWENTIETH_CENTURY_BOYS),
    G("F25 sequel of F24", F("21st Century Boys", Vols("21st Century Boys", 2)), S, AUTO, TWENTY_FIRST_CENTURY_BOYS),
    G("F26 punctuation title", F("Yotsuba to!", Vols("Yotsuba to!", 15)), S, AUTO, YOTSUBA),
    G("F27 x-titled, look-alike supersets", F("Hunter x Hunter", Vols("Hunter x Hunter", 37)), S, AUTO, HUNTER_X_HUNTER),
    G("F28 three same-titled records (disambiguated)", F("Jigokuraku [Hell's Paradise]", Vols("Hell's Paradise - Jigokuraku", 13, " (2019)")), S, REVIEW, JIGOKURAKU_KAKU),
    G("F29 long vowel romaji", F("Sousou no Frieren", Vols("Sousou no Frieren", 13)), S, AUTO, FRIEREN),
    G("F30 English with subtitle and apostrophe", F("Frieren - Beyond Journey's End", Vols("Frieren - Beyond Journey's End", 13)), S, AUTO, FRIEREN),
    G("F31 x-titled", F("Spy x Family", Vols("Spy x Family", 13)), S, AUTO, SPY_X_FAMILY),
    G("F32 webtoon chapters under Manhwa", F("Tower of God", Units(600), "Manhwa"), S, AUTO, TOWER_OF_GOD),
    G("F33 English webtoon name with apostrophe", F("Omniscient Reader's Viewpoint", Units(200), "Manhwa"), S, AUTO, OMNISCIENT_READER),
    G("F34 generic one-word title under an author folder", F("Monster", Vols("Monster", 18), parent="Urasawa Naoki"), S, AUTO, MONSTER_URASAWA),
    G("F35 one-word title", F("Akira", Vols("Akira", 6)), S, AUTO, AKIRA),
    G("F36 romaji", F("Oyasumi Punpun", Vols("Oyasumi Punpun", 13)), S, AUTO, PUNPUN),
    G("F37 volumes", F("Fire Punch", Vols("Fire Punch", 8)), S, AUTO, FIRE_PUNCH),
    G("F38 nothing on the provider", F("Zzqx Nonexistent Synthetic Title", Vols("Zzqx Nonexistent Synthetic Title", 3)), S, UNMATCHED),
    G("F39 files older than the series: year conflict", F("Berserk", Vols("Berserk", 5, " (1985)")), S, REVIEW, BERSERK, vetoes=MatchReason.YEAR_CONFLICT),
    G("F40 unit subfolders only", F("Chainsaw Man", [], subs=[("Volumes", 11), ("Chapters", 80)]), SWU, AUTO, CHAINSAW_MAN),
    G("F41 leading scan-group tag on the folder", F("[Scan Team] Hunter x Hunter", Vols("Hunter x Hunter", 37)), S, AUTO, HUNTER_X_HUNTER),
    G("F42 underscores", F("Sousou_no_Frieren", Vols("Sousou_no_Frieren", 13)), S, AUTO, FRIEREN),
    G("F43 doujinshi allowed: dj look-alikes stay below the series", F("Yotsuba to!", Vols("Yotsuba to!", 15)), S, AUTO, YOTSUBA, doujin_allowed=True),
    G("F44 category agrees", F("Shingeki no Kyojin", Units(139), "Manga"), S, AUTO, ATTACK_ON_TITAN),
    G("F45 season subfolders", F("Tower of God", [], "Manhwa", subs=[("Season 1", 80), ("Season 2", 330), ("Season 3", 190)]), SWU, AUTO, TOWER_OF_GOD),

    # --- Archive level: artist folder, collection leaf, doujin ------------------------------
    G("A01 artist folder: one-shot", F("Fujimoto Tatsuki", ARTIST_FOLDER), WorkClass.ARTIST_COLLECTION, AUTO, LOOK_BACK, group_title="Look Back"),
    G("A02 artist folder: second one-shot", F("Fujimoto Tatsuki", ARTIST_FOLDER), WorkClass.ARTIST_COLLECTION, AUTO, SAYONARA_ERI, group_title="Sayonara Eri"),
    G("A03 artist folder: numbered volumes grouped", F("Fujimoto Tatsuki", ARTIST_FOLDER), WorkClass.ARTIST_COLLECTION, AUTO, FIRE_PUNCH, group_title="Fire Punch"),
    G("A04 artist folder: a mis-tagged volume, the author conflict alone vetoes auto", F("Fujimoto Tatsuki", ARTIST_FOLDER), WorkClass.ARTIST_COLLECTION, REVIEW, BERSERK,
      group_title="Berserk", vetoes=MatchReason.AUTHOR_CONFLICT),
    G("A05 collection leaf: a lone volume of a long series", F("Shelf", COLLECTION_LEAF), WorkClass.COLLECTION_LEAF, AUTO, HUNTER_X_HUNTER, group_title="Hunter x Hunter"),
    G("A06 collection leaf: one-shot", F("Shelf", COLLECTION_LEAF), WorkClass.COLLECTION_LEAF, AUTO, LOOK_BACK, group_title="Look Back"),
    G("A07 doujin anatomy: numbered dj mini-series, parody dj form, author agrees", F("Doujin Shelf", DOUJIN_SHELF), WorkClass.COLLECTION_LEAF, AUTO,
      YOTSUBA_DJ_YANDA, doujin_allowed=True, group_title="Yanda&", content=ContentSuggestion.DOUJINSHI_AND_ADULT_ONE_SHOTS),
    G("A08 ambiguous folder: review only", F("Vinland Saga",
      ["Vinland Saga 1.cbz", "Vinland Saga 2.cbz", "Vinland Saga 3.cbz", "Alpha Story.cbz", "Beta Tale.cbz", "Gamma Saga.cbz"]),
      WorkClass.AMBIGUOUS, REVIEW, VINLAND_SAGA),
    G("A10 doujin anatomy: a lone archive of a 7-volume dj record links to it (one archive may hold the whole series)", F("Doujin Shelf", DOUJIN_SHELF_LONE), WorkClass.COLLECTION_LEAF,
      AUTO, YOTSUBA_DJ_YANDA, doujin_allowed=True, group_title="Yanda&", content=ContentSuggestion.DOUJINSHI_AND_ADULT_ONE_SHOTS),
    G("A09 mixed folder: review only", F("Berserk", ["Berserk v01.cbz"], subs=[("Berserk Gaiden", 2)]), WorkClass.MIXED, REVIEW, BERSERK),

    # --- Detector only ---------------------------------------------------------------------
    G("D01 category container", F("Manga", [], depth=1, subs=[("Berserk", 41), ("Vinland Saga", 12), ("One Piece", 1100)]), WorkClass.COLLECTION_CONTAINER),
    G("D02 franchise container of numbered parts", F("JoJo no Kimyou na Bouken", [],
      subs=[("JoJo no Kimyou na Bouken Part 3 - Stardust Crusaders", 16), ("JoJo no Kimyou na Bouken Part 4 - Diamond wa Kudakenai", 18)]), WorkClass.FRANCHISE_CONTAINER),
    G("D03 wrapper", F("Berserk Collection", [], subs=[("Berserk", 41)]), WorkClass.WRAPPER),
    G("D04 unit subfolder", F("Volumes", Vols("Chainsaw Man", 11), depth=3), WorkClass.UNIT_SUB),
    G("D05 season subfolder", F("Season 2", Units(330), depth=3), WorkClass.UNIT_SUB),
    G("D06 library root", F("Library", Vols("Berserk", 3), depth=0), WorkClass.EXCLUDED),
)
