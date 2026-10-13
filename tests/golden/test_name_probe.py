"""MangaPixer 1.42.0 name parity beyond the golden set (which has no watermarked, three-decimal, index-prefixed or dated
doujin names): ``mangapixer_name_probe.txt`` is MangaPixer's own reading of synthetic names - units, cleaned title,
base title, anatomy, dated doujin parse, matcher integers, creator hints, the one-archive folder plan, an author's
name forms and release tags - and MangaList's port must print every line the same."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from mangalist.matcher import anatomy, dated_doujin, detector, planner
from mangalist.matcher import auto_match_text as amt
from mangalist.matcher.contracts import FolderShape
from mangalist.matcher.normalizer import archive_base_title, normalize

REFERENCE = Path(__file__).resolve().parent / "mangapixer_name_probe.txt"


def _d(x) -> str:
    return "" if x is None else str(x)


def _s(x) -> str:
    return "<null>" if x is None else x


def _pascal(e) -> str:
    return "".join(p.capitalize() for p in e.name.split("_"))


def name_lines(name: str) -> List[str]:
    """The probe's lines for one archive name, in the reference's format."""
    u = amt.units_of(name)
    n = normalize(name)
    a = anatomy.parse(name)
    dd = dated_doujin.try_parse(name)
    folder = FolderShape(display_name=os.path.splitext(name)[0], depth=2, archive_names=(name,), subfolders=())
    c = detector.classify(folder)
    plan = planner.plan_folder(folder, c)
    return [
        f"UNITS|{name}|{_d(u.volume)}|{_d(u.volume_end)}|{_d(u.chapter)}|{_d(u.chapter_end)}|{u.is_extra}",
        f"NORM|{name}|{n.primary}|{';'.join(x.text for x in n.derived)}",
        f"BASE|{name}|{archive_base_title(name)}",
        f"ANAT|{name}|{_s(a.leading_tag)}|{_s(a.circle)}|{_s(a.artist)}|{_s(a.parody)}|{a.is_dated}"
        f"|{a.is_doujin_shaped}|{a.title}|{';'.join(a.creator_tags)}",
        f"DATED|{name}|none" if dd is None
        else f"DATED|{name}|{dd.creator}|{_s(dd.title)}|{_s(dd.character)}|{';'.join(dd.tags)}",
        f"NUM|{name}|{_d(amt.chapter_number_of(name))}|{_d(amt.volume_number_of(name))}|{amt.is_chapter_like(name)}"
        f"|{amt.is_volume_like(name)}",
        f"HINTS|{name}|{';'.join(amt.creator_hints(name))}",
        f"PLAN|{name}|{_pascal(c.cls)}|{';'.join(v.text + '=' + _pascal(v.kind) for v in plan.variants)}",
    ]


def _reference() -> List[str]:
    return [line for line in REFERENCE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


def test_every_probe_line_reads_as_in_mangapixer_1_42():
    reference = _reference()
    inputs: Dict[str, List[str]] = {"UNITS": [], "AUTHOR": [], "TAG": []}
    fields_after = {"UNITS": 5, "AUTHOR": 1, "TAG": 1}         # a name may hold a "|" itself: split from the right
    for line in reference:
        kind, _, rest = line.partition("|")
        if kind in inputs:
            inputs[kind].append(rest.rsplit("|", fields_after[kind])[0])
    ours: List[str] = [line for name in inputs["UNITS"] for line in name_lines(name)]
    ours += [f"AUTHOR|{a}|{';'.join(amt.author_name_forms(a))}" for a in inputs["AUTHOR"]]
    ours += [f"TAG|{t}|{anatomy.is_release_tag(t)}" for t in inputs["TAG"]]

    assert len(inputs["UNITS"]) == 77 and len(ours) == len(reference) == 77 * 8 + 7 + 20
    differences = [f"  ours: {o}\n  ref:  {r}" for o, r in zip(ours, reference) if o != r]
    assert not differences, "Differences from MangaPixer 1.42.0:\n" + "\n".join(differences)
