"""The stage-2 golden set (port of MangaPixer 1.31.1 ``GoldenSetTests.cs``).

Every case runs the real detector, planner, scorer AND the production retrieval loop
(``mangalist.matcher.retrieval``) and provider mapping (``mangalist.matcher.mangaupdates``) over RECORDED
MangaUpdates responses - no network. Asserts the class, band and chosen id per case, then compares every
case (class, band, chosen id, scores, reasons, and the searches and GETs sent) and the aggregate numbers
with MangaPixer's own run of the same cases (``mangapixer_report.txt``), so any divergence between the port
and the reference shows up by case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from mangalist.matcher import (
    DEFAULT_THRESHOLDS,
    MatchBand,
    MatchOutcome,
    MatchThresholds,
    WorkClass,
    WorkClassification,
    reasons_text,
)
from mangalist.matcher import detector, planner, scorer
from mangalist.matcher._text import format_fixed
from mangalist.matcher.mangaupdates import SearchPage, map_search_page, map_series
from mangalist.matcher.retrieval import retrieve_and_score

from .golden_cases import ALL, GoldenCase

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REFERENCE_REPORT = HERE / "mangapixer_report.txt"

BAND_NAMES = {MatchBand.AUTO: "Auto", MatchBand.NEEDS_REVIEW: "NeedsReview", MatchBand.UNMATCHED: "Unmatched"}


def class_name(cls: WorkClass) -> str:
    """The reference's enum name (``SERIES_WITH_UNITS`` -> ``SeriesWithUnits``)."""
    return "".join(part.capitalize() for part in cls.name.split("_"))


@lru_cache(maxsize=1)
def _fixtures() -> Tuple[Dict[Tuple[str, bool, int], dict], Dict[str, dict]]:
    searches: Dict[Tuple[str, bool, int], dict] = {}
    series: Dict[str, dict] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        root = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith("search."):
            doujin_allowed = "Doujinshi" not in root["filter_types"]
            searches[(root["query"], doujin_allowed, int(root.get("page", 1)))] = root["response"]
        elif path.name.startswith("series."):
            series[str(root["series_id"])] = root
    return searches, series


@dataclass(frozen=True)
class Run:
    classification: WorkClassification
    outcome: Optional[MatchOutcome]
    missing: Tuple[str, ...]
    searches: int
    gets: int


def execute(c: GoldenCase, thresholds: MatchThresholds = DEFAULT_THRESHOLDS) -> Run:
    classification = detector.classify(c.folder)
    if c.band is None:
        return Run(classification, None, (), 0, 0)

    if c.group_title is None:
        query = planner.plan_folder(c.folder, classification, c.comic_info)
    else:
        group = next(g for g in classification.archive_groups if g.query_title == c.group_title)
        query = planner.plan_archive_group(c.folder, classification, group)

    searches_by_query, series_by_id = _fixtures()
    missing: List[str] = []

    # A request without a recording is noted (the exact request the set needs) and answered like the
    # reference's harness: no hits / not found.
    def search(text: str, page: int) -> SearchPage:
        response = searches_by_query.get((text, c.doujin_allowed, page))
        if response is None:
            missing.append(json.dumps({"kind": "search", "query": text, "doujin": c.doujin_allowed, "page": page}))
            return SearchPage((), 0)
        return map_search_page(response)

    def get(external_id: str):
        record = series_by_id.get(external_id)
        if record is None:
            missing.append(json.dumps({"kind": "get", "id": external_id}))
            return None
        return map_series(record, external_id)

    result = retrieve_and_score(query, search, get, thresholds)
    outcome = result.outcome if not missing else None
    return Run(classification, outcome, tuple(missing), result.searches, result.gets)


def _detail(outcome: MatchOutcome) -> str:
    if not outcome.ranked:
        return "no candidates"
    top = outcome.ranked[0]
    text = (f"top {top.candidate.external_id} '{top.candidate.title}' title {format_fixed(top.title_score, 3)} "
            f"adj {format_fixed(top.adjusted_score, 3)} [{reasons_text(top.reasons)}]")
    if len(outcome.ranked) > 1:
        second = outcome.ranked[1]
        text += (f"; 2nd {second.candidate.external_id} '{second.candidate.title}' "
                 f"adj {format_fixed(second.adjusted_score, 3)}")
    return text


def test_case_count_matches_the_reference():
    assert len(ALL) == 85
    assert sum(1 for c in ALL if c.band is not None) == 77


@pytest.mark.parametrize("case", ALL, ids=[c.id.split(" ")[0] for c in ALL])
def test_case(case: GoldenCase):
    run = execute(case)

    assert not run.missing, "Missing fixtures:\n" + "\n".join(run.missing)
    if case.cls is not None:
        assert run.classification.cls == case.cls
    if case.content is not None:
        assert run.classification.content_suggestion == case.content
    if case.band is None:
        return

    outcome = run.outcome
    detail = _detail(outcome)
    assert outcome.band == case.band, f"band {outcome.band.name}, expected {case.band.name}: {detail}"
    top = outcome.ranked[0] if outcome.ranked else None
    if case.vetoes is not None:
        assert top.reasons & scorer.VETO_REASONS == case.vetoes
    if case.expected_id is not None:
        assert top is not None and top.candidate.external_id == case.expected_id, \
            f"chosen {top.candidate.external_id if top else None}, expected {case.expected_id}: {detail}"


def _case_lines(c: GoldenCase) -> List[str]:
    """The case in the reference report's format: its class, and for a scored case the band line and the
    requests it sent."""
    key = c.id.split(" ")[0]
    run = execute(c)
    lines = [f"CLASS {key}: {class_name(run.classification.cls)}"]
    if c.band is None:
        return lines
    o = run.outcome
    top = o.ranked[0] if o is not None and o.ranked else None
    lines.append(f"{c.id}: {BAND_NAMES[o.band]} {top.candidate.external_id if top else ''} "
                 f"title {format_fixed(top.title_score, 3) if top else ''} adj {format_fixed(top.adjusted_score, 3) if top else ''} "
                 f"[{reasons_text(top.reasons) if top else ''}]")
    lines.append(f"REQUESTS {key}: {run.searches} searches + {run.gets} GETs")
    return lines


def aggregate(thresholds: MatchThresholds) -> Tuple[str, int, int]:
    matched = auto = auto_correct = review = review_top_correct = review_with_expected = unmatched = 0
    searches = gets = 0
    for c in (c for c in ALL if c.band is not None):
        run = execute(c, thresholds)
        if run.outcome is None:
            continue
        o = run.outcome
        matched += 1
        searches += run.searches
        gets += run.gets
        top_id = o.ranked[0].candidate.external_id if o.ranked else None
        if o.band == MatchBand.AUTO:
            auto += 1
            if c.expected_id is not None and top_id == c.expected_id:
                auto_correct += 1
        elif o.band == MatchBand.NEEDS_REVIEW:
            review += 1
            if c.expected_id is not None:
                review_with_expected += 1
                if top_id == c.expected_id:
                    review_top_correct += 1
        else:
            unmatched += 1

    searches_by_query, series_by_id = _fixtures()
    precision = 0 if auto == 0 else 100.0 * auto_correct / auto
    report = (f"matched cases {matched}: auto {auto} ({format_fixed(100.0 * auto / matched, 1)}%), review {review}, "
              f"unmatched {unmatched}; auto precision {auto_correct}/{auto} ({format_fixed(precision, 1)}%); "
              f"review top correct {review_top_correct}/{review_with_expected}; requests {searches} searches + {gets} GETs "
              f"({format_fixed((searches + gets) / matched, 2)} per work); fixtures {len(searches_by_query)} searches, "
              f"{len(series_by_id)} series")
    return report, auto, auto_correct


SWEEP = (
    ("default", DEFAULT_THRESHOLDS),
    ("loosest", MatchThresholds(MatchThresholds.AUTO_TITLE_MIN, MatchThresholds.MARGIN_MIN, MatchThresholds.REVIEW_FLOOR_MIN)),
    ("strictest", MatchThresholds(MatchThresholds.AUTO_TITLE_MAX, MatchThresholds.MARGIN_MAX, MatchThresholds.REVIEW_FLOOR_MAX)),
)


def _line_key(line: str) -> str:
    """``CLASS F01`` / ``REQUESTS F01`` / ``F01`` (a case line) / ``GOLDEN default``."""
    head = line.split(": ", 1)[0]
    if head.startswith(("CLASS ", "REQUESTS ", "GOLDEN ")):
        return head
    return head.split(" ", 1)[0]


def _reference_lines() -> Dict[str, str]:
    return {_line_key(line): line for line in REFERENCE_REPORT.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def test_report_and_reference_comparison(capsys):
    """Prints the per-case and aggregate report and requires every line to be identical to MangaPixer's own
    run of the same cases (``mangapixer_report.txt``): same detector class, band, chosen id, title and
    adjusted score (3 decimals), reasons and request counts per case, and the same aggregate at all three
    threshold settings. At the defaults every auto link must also be right."""
    reference = _reference_lines()
    lines = [line for c in ALL for line in _case_lines(c)]
    differences = [f"  ours: {line}\n  ref:  {reference.get(_line_key(line))}"
                   for line in lines if reference.get(_line_key(line)) != line]

    with capsys.disabled():
        print()
        for line in lines:
            if not line.startswith(("CLASS ", "REQUESTS ")):
                print(line)
        for name, thresholds in SWEEP:
            report, auto, auto_correct = aggregate(thresholds)
            line = f"GOLDEN {name}: {report}"
            print(line)
            lines.append(line)
            if reference.get(f"GOLDEN {name}") != line:
                differences.append(f"  ours: {line}\n  ref:  {reference.get(f'GOLDEN {name}')}")
            if name == "default":
                assert auto == auto_correct, "a wrong auto link at the default thresholds"

    assert len(reference) == len(lines) == 85 + 2 * 77 + 3
    assert not differences, "Differences from MangaPixer's report:\n" + "\n".join(differences)
