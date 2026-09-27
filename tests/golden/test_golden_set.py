"""The stage-2 golden set (port of MangaPixer 1.26.0 ``GoldenSetTests.cs``).

Every case runs the real detector, planner, scorer AND the production retrieval loop
(``manga_list.matcher.retrieval``) over RECORDED MangaUpdates responses - no network. Asserts the
class, band and chosen id per case, then compares every case and the aggregate numbers with
MangaPixer's own golden report at v1.26.0 (``mangapixer_v1.26.0_report.txt``), so any divergence
between the port and the reference shows up by case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from manga_list.matcher import (
    DEFAULT_THRESHOLDS,
    MatchBand,
    MatchCandidate,
    MatchOutcome,
    MatchThresholds,
    WorkClassification,
    reasons_text,
)
from manga_list.matcher import detector, planner, scorer
from manga_list.matcher._text import format_fixed
from manga_list.matcher.mangaupdates import map_search_hit, map_series
from manga_list.matcher.retrieval import retrieve_and_score

from .golden_cases import ALL, GoldenCase

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REFERENCE_REPORT = HERE / "mangapixer_v1.26.0_report.txt"

BAND_NAMES = {MatchBand.AUTO: "Auto", MatchBand.NEEDS_REVIEW: "NeedsReview", MatchBand.UNMATCHED: "Unmatched"}


class MissingFixture(Exception):
    pass


@lru_cache(maxsize=1)
def _fixtures() -> Tuple[Dict[Tuple[str, bool], List[MatchCandidate]], Dict[str, dict]]:
    searches: Dict[Tuple[str, bool], List[MatchCandidate]] = {}
    series: Dict[str, dict] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        root = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith("search."):
            doujin_allowed = "Doujinshi" not in root["filter_types"]
            hits = [map_search_hit(r) for r in root["response"]["results"]]
            searches[(root["query"], doujin_allowed)] = hits
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

    def search(text: str):
        hits = searches_by_query.get((text, c.doujin_allowed))
        if hits is None:
            missing.append(json.dumps({"kind": "search", "query": text, "doujin": c.doujin_allowed}))
            raise MissingFixture(missing[-1])
        return hits

    def get(external_id: str):
        record = series_by_id.get(external_id)
        if record is None:
            missing.append(json.dumps({"kind": "get", "id": external_id}))
            return None
        return map_series(record)

    try:
        result = retrieve_and_score(query, search, get, thresholds)
    except MissingFixture:
        return Run(classification, None, tuple(missing), 0, 0)
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
    assert len(ALL) == 61
    assert sum(1 for c in ALL if c.band is not None) == 55


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


def _case_line(c: GoldenCase) -> str:
    """One line in the reference's per-case report format."""
    o = execute(c).outcome
    top = o.ranked[0] if o is not None and o.ranked else None
    return (f"{c.id}: {BAND_NAMES[o.band]} {top.candidate.external_id if top else ''} "
            f"title {format_fixed(top.title_score, 3) if top else ''} adj {format_fixed(top.adjusted_score, 3) if top else ''} "
            f"[{reasons_text(top.reasons) if top else ''}]")


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


def _reference_lines() -> Tuple[Dict[str, str], Dict[str, str]]:
    per_case: Dict[str, str] = {}
    aggregates: Dict[str, str] = {}
    for line in REFERENCE_REPORT.read_text(encoding="utf-8").splitlines():
        if line.startswith("GOLDEN "):
            name, _, rest = line[len("GOLDEN "):].partition(": ")
            aggregates[name] = rest
        elif line.strip():
            case_id = line.split(": ", 1)[0]
            per_case[case_id] = line
    return per_case, aggregates


def test_report_and_reference_comparison(capsys):
    """Prints the per-case and aggregate report (as MangaPixer's CI does) and requires every line to be
    identical to MangaPixer's own run of the same set at v1.26.0: same band, chosen id, title and
    adjusted score (3 decimals) and reasons per case, and the same aggregate at all three threshold
    settings. At the defaults every auto link must also be right."""
    reference_cases, reference_aggregates = _reference_lines()
    lines = [_case_line(c) for c in ALL if c.band is not None]
    differences = [f"  ours: {line}\n  ref:  {reference_cases.get(line.split(': ', 1)[0])}"
                   for line in lines if reference_cases.get(line.split(": ", 1)[0]) != line]

    with capsys.disabled():
        print()
        for line in lines:
            print(line)
        for name, thresholds in SWEEP:
            report, auto, auto_correct = aggregate(thresholds)
            print(f"GOLDEN {name}: {report}")
            if reference_aggregates.get(name) != report:
                differences.append(f"  ours: GOLDEN {name}: {report}\n  ref:  GOLDEN {name}: {reference_aggregates.get(name)}")
            if name == "default":
                assert auto == auto_correct, "a wrong auto link at the default thresholds"

    assert len(reference_cases) == 55
    assert not differences, "Differences from MangaPixer v1.26.0:\n" + "\n".join(differences)
