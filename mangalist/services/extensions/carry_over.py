"""Carry the owner's Suwayomi setup over to MangaList's own extension host, once and safely. No Qt.

Owner (2026-10-23): "bring the built-in suwayumi replacement into main. We will now proceed with only using the built in
extensions and disabling suwayumi". Each series' chapter source was picked under Suwayomi (``suwayomi.series.<id>``:
the Suwayomi manga, by the MangaDex id MangaPixer links or by a title match the owner confirmed). Manga ids are each
program's own, so the host keeps its choices apart (``extensions.series.<id>``) - without this, switching means picking
the source again for every series. Source ids are the same in both programs (Tachiyomi's deterministic ids), and so
is a manga's url on its source: that is what is carried.

:func:`carry_over` does, in this order:

1. **Repositories**: Suwayomi's extension repositories the host lacks are added to the host (it checks each index and
   its signing key first) and remembered for the host's next start, as Settings' own "Add repository" does.
2. **Extensions**: the extensions the series' sources (and the allowed sources of Download sources) need that the host
   does not have are installed from the host's repositories - from the repository Suwayomi installed them from when the
   host lists it - signature-checked as every install. One that cannot be installed is reported, with the host's reason.
3. **Series**: for every Suwayomi choice, Suwayomi is asked for the manga's url / title / source (read-only queries,
   :data:`MANGAS_PER_CALL` per call), the host registers it (``POST /compat/manga``: get-or-create by source and url) and
   ``extensions.series.<id>`` is written with the same ``how``, title and source and the host's manga id. A series that
   already has a manga on the host is never overwritten; Suwayomi's choice is never touched (switching back stays
   possible).

Each series ends as one of :data:`STATUSES` (carried, already there, source not installed, Suwayomi does not know the
manga, failed). A second run carries nothing new. It is polite: one call at a time, a short pause between them, no
request to any source's site (registering a manga is the host's own bookkeeping; its details and chapters are fetched
at the first chapter lookup, as for a manga a search found). ``dry_run`` asks both programs and reports what it would do
without writing anything - to MangaList, to the host or to Suwayomi (which is only ever read).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ...store.db import utcnow
from ...store.downloads import SuwayomiSettings
from ..suwayomi.client import SuwayomiError
from .client import ExtensionHostError, HostUnreachable, NotFound

_log = logging.getLogger(__name__)

CARRIED = "carried"
ALREADY = "already there"
NO_SOURCE = "source not installed"
UNKNOWN = "Suwayomi does not know the manga"
FAILED = "failed"
STATUSES = (CARRIED, ALREADY, NO_SOURCE, UNKNOWN, FAILED)

#: An extension's state after the run.
EXT_PRESENT = "already installed"
EXT_INSTALLED = "installed"
EXT_WOULD_INSTALL = "would be installed"
EXT_NOT_INSTALLED = "not installed"         # install=False
EXT_FAILED = "could not be installed"
EXT_NOT_LISTED = "not in the host's repositories"

#: Seconds between two calls to a program (polite: no request storm).
DEFAULT_PAUSE = 0.05

Progress = Callable[[int, int, str], None]


class CarryOverError(Exception):
    """The carry-over could not start (Suwayomi or the host not reachable, Suwayomi not set up); nothing was written."""


@dataclass
class SeriesCarry:
    """One series' result."""

    series_id: int
    status: str
    how: str = ""
    title: str = ""
    source_id: str = ""
    source_name: str = ""
    suwayomi_manga_id: Optional[int] = None
    host_manga_id: Optional[int] = None
    message: str = ""


@dataclass
class ExtensionNeed:
    """An extension the carried series (or the allowed sources) need."""

    pkg: str
    name: str = ""
    state: str = EXT_PRESENT
    repo: str = ""
    source_ids: Tuple[str, ...] = ()
    series: int = 0                       # how many series use its sources
    message: str = ""


@dataclass
class CarryReport:
    dry_run: bool = False
    series: List[SeriesCarry] = field(default_factory=list)
    extensions: List[ExtensionNeed] = field(default_factory=list)
    repos_added: List[str] = field(default_factory=list)          # added (dry run: would be added)
    repos_failed: Dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for r in self.series:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def of(self, status: str) -> List[SeriesCarry]:
        return [r for r in self.series if r.status == status]

    @property
    def to_install(self) -> List[ExtensionNeed]:
        return [e for e in self.extensions if e.state in (EXT_WOULD_INSTALL, EXT_INSTALLED)]

    @property
    def extension_problems(self) -> List[ExtensionNeed]:
        return [e for e in self.extensions if e.state in (EXT_FAILED, EXT_NOT_LISTED, EXT_NOT_INSTALLED)]

    def summary(self) -> str:
        """One line: ``Carried 210, already there 5, source not installed 2`` (dry run: ``Would carry ...``)."""
        counts = self.counts()
        parts = [f"{'would carry' if self.dry_run else 'carried'} {counts[CARRIED]}"]
        parts += [f"{s} {counts[s]}" for s in STATUSES[1:] if counts[s]]
        text = ", ".join(parts)
        return text[:1].upper() + text[1:]

    def lines(self, *, every_series: bool = False) -> List[str]:
        """The report as text lines (the CLI prints them; Settings shows them): what was (or would be) done, then each
        series that was not carried, with its reason (*every_series*: each carried one too)."""
        verb = "Would" if self.dry_run else "Did"
        out = [f"{verb} carry over from Suwayomi: {self.summary()} (of {len(self.series)} series)"]
        if self.repos_added:
            out.append(("Repositories to add: " if self.dry_run else "Repositories added: ") + ", ".join(self.repos_added))
        for url, why in self.repos_failed.items():
            out.append(f"Repository not added: {url}: {why}")
        installs = self.to_install
        if installs:
            out.append(("Extensions to install: " if self.dry_run else "Extensions installed: ")
                       + ", ".join(f"{e.name or e.pkg} ({e.series} series)" for e in installs))
        for e in self.extension_problems:
            out.append(f"Extension {e.state}: {e.name or e.pkg} ({e.series} series)" + (f": {e.message}" if e.message else ""))
        present = [e for e in self.extensions if e.state == EXT_PRESENT]
        if present:
            out.append("Extensions already in the host: " + ", ".join(e.name or e.pkg for e in present))
        for r in self.series:
            if r.status == CARRIED and not every_series:
                continue
            target = f" -> host manga {r.host_manga_id}" if r.host_manga_id is not None else ""
            out.append(f"  series {r.series_id} {r.title!r} ({r.source_name or r.source_id or '?'}, Suwayomi manga "
                       f"{r.suwayomi_manga_id}{target}): {r.status}" + (f" - {r.message}" if r.message else ""))
        return out


# --- the run ------------------------------------------------------------------------------------------------------

def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sleep(pause: float, first: bool) -> None:
    if pause and not first:
        time.sleep(pause)


def carry_over(db, suwayomi_client, host_client, *, install: bool = True, dry_run: bool = False,
               progress: Optional[Progress] = None, pause: float = DEFAULT_PAUSE) -> CarryReport:
    """Carry every Suwayomi per-series choice over to the extension host (see the module docstring). *suwayomi_client*:
    a :class:`~mangalist.services.suwayomi.SuwayomiClient` (only read); *host_client*: an
    :class:`~mangalist.services.extensions.client.ExtensionHostClient`. *install*: install the extensions the host
    lacks; *dry_run*: write nothing anywhere, report what would be done. *progress(done, total, text)* is called as it
    goes (from the calling thread). Raises :class:`CarryOverError` when either program cannot be asked at the start."""
    started = time.monotonic()
    tell = progress or (lambda done, total, text: None)
    settings = SuwayomiSettings(db)
    report = CarryReport(dry_run=dry_run)
    choices = {sid: c for sid, c in settings.suwayomi_series_choices().items() if _int(c.get("manga_id")) is not None}
    on_host = settings.extensions_series_choices()

    # --- ask both programs (Suwayomi: read-only) ---
    tell(0, len(choices), "Asking Suwayomi for the series' mangas, extensions and repositories...")
    try:
        suw_sources = {s.id: s for s in suwayomi_client.sources()}
        suw_exts = {e.pkg: e for e in suwayomi_client.installed_extensions()}
        suw_repos = suwayomi_client.extension_repos()
        wanted = sorted({int(c["manga_id"]) for sid, c in choices.items()
                         if _int((on_host.get(sid) or {}).get("manga_id")) is None})
        suw_mangas = suwayomi_client.mangas_by_id(wanted, pause=pause) if wanted else {}
    except SuwayomiError as exc:
        raise CarryOverError(f"Suwayomi could not be asked: {exc}") from None
    tell(0, len(choices), "Asking the extension host for its repositories and extensions...")
    try:
        host_repos = [r.url for r in host_client.repos()]
        listing = host_client.repo()
        host_exts = {e.pkg: e for e in host_client.extensions()}
        host_sources = {s.id for s in host_client.sources()}
    except ExtensionHostError as exc:
        raise CarryOverError(f"the extension host could not be asked: {exc}") from None

    # --- 1. repositories ---
    missing_repos = [r.url for r in suw_repos if r.url not in host_repos]
    for i, url in enumerate(missing_repos):
        if dry_run:
            report.repos_added.append(url)
            continue
        _sleep(pause, i == 0)
        tell(0, len(choices), f"Adding the repository {url}...")
        try:
            host_client.add_repo(url)
            report.repos_added.append(url)
        except ExtensionHostError as exc:
            report.repos_failed[url] = str(exc)
    if report.repos_added and not dry_run:
        try:
            listing = host_client.repo()
            host_repos = [r.url for r in host_client.repos()]
        except ExtensionHostError as exc:
            raise CarryOverError(f"the extension host stopped answering: {exc}") from None
        config = settings.extension_host()
        settings.save_extension_host(replace(config, repos=tuple(host_repos)))

    # --- 2. extensions ---
    pkg_of_source: Dict[str, str] = {}
    names: Dict[str, str] = {}
    listed: Dict[str, List[str]] = {}                      # pkg -> the repositories listing it
    for entry in listing:
        listed.setdefault(entry.pkg, []).append(entry.repo)
        names.setdefault(entry.pkg, entry.name)
        for s in entry.sources:
            pkg_of_source.setdefault(s.id, entry.pkg)
    for pkg, ext in host_exts.items():
        names.setdefault(pkg, ext.name)
        for s in ext.sources:
            pkg_of_source.setdefault(s.id, pkg)
    for sid, src in suw_sources.items():
        if src.extension:
            pkg_of_source.setdefault(sid, src.extension)
    for pkg, ext in suw_exts.items():
        names.setdefault(pkg, ext.name)

    def source_of(sid: int, choice: Mapping[str, Any]) -> str:
        manga = suw_mangas.get(int(choice["manga_id"]))
        return str(manga.source_id if manga is not None and manga.source_id else choice.get("source_id") or "")

    series_per_source: Dict[str, int] = {}
    for sid, choice in choices.items():
        source = source_of(sid, choice)
        if source:
            series_per_source[source] = series_per_source.get(source, 0) + 1
    needed_sources = list(series_per_source) + [s for s in settings.sources() or () if s not in series_per_source]
    needs: Dict[str, ExtensionNeed] = {}
    unmapped: List[str] = []
    for source in needed_sources:
        pkg = pkg_of_source.get(source)
        if pkg is None:
            unmapped.append(source)
            continue
        need = needs.setdefault(pkg, ExtensionNeed(pkg=pkg, name=names.get(pkg, ""),
                                                   repo=getattr(suw_exts.get(pkg), "repo", "") or ""))
        need.source_ids += (source,)
        need.series += series_per_source.get(source, 0)
    for i, need in enumerate(sorted(needs.values(), key=lambda n: (-n.series, n.pkg))):
        report.extensions.append(need)
        if need.pkg in host_exts:
            need.state = EXT_PRESENT
            continue
        if need.pkg not in listed:
            need.state = EXT_NOT_LISTED
            need.message = "add the repository it comes from (Settings > Built-in extensions > Extensions)"
            continue
        if dry_run:
            need.state = EXT_WOULD_INSTALL if install else EXT_NOT_INSTALLED
            continue
        if not install:
            need.state = EXT_NOT_INSTALLED
            continue
        repo = need.repo if need.repo in listed[need.pkg] else None
        _sleep(pause, i == 0)
        tell(0, len(choices), f"Installing {need.name or need.pkg}...")
        try:
            ext = host_client.install(need.pkg, repo=repo)
        except ExtensionHostError as exc:
            need.state, need.message = EXT_FAILED, str(exc)
            _log.warning("Carry-over: %s could not be installed: %s", need.pkg, exc)
            continue
        need.state = EXT_INSTALLED
        host_sources.update(s.id for s in ext.sources)
        _log.info("Carry-over: installed %s %s", ext.name, ext.version)
    will_have = set(host_sources)
    if dry_run:
        for need in report.extensions:
            if need.state == EXT_WOULD_INSTALL:
                will_have.update(need.source_ids)
    elif any(n.state == EXT_INSTALLED for n in report.extensions):
        try:
            host_sources = {s.id for s in host_client.sources()}
            will_have = set(host_sources)
        except ExtensionHostError as exc:
            raise CarryOverError(f"the extension host stopped answering: {exc}") from None
    need_of_source = {s: n for n in report.extensions for s in n.source_ids}
    display = {sid: s.display_name for sid, s in suw_sources.items()}

    # --- 3. the series ---
    total = len(choices)
    host_down = ""
    first = True
    for done, sid in enumerate(sorted(choices), start=1):
        choice = choices[sid]
        suw_id = int(choice["manga_id"])
        source = source_of(sid, choice)
        row = SeriesCarry(series_id=sid, status=FAILED, how=str(choice.get("how") or ""),
                          title=str(choice.get("manga_title") or ""), source_id=source,
                          source_name=display.get(source, ""), suwayomi_manga_id=suw_id)
        report.series.append(row)
        existing = on_host.get(sid) or {}
        if _int(existing.get("manga_id")) is not None:
            row.status, row.host_manga_id = ALREADY, _int(existing.get("manga_id"))
            continue
        manga = suw_mangas.get(suw_id)
        if manga is None:
            row.status = UNKNOWN
            row.message = "the manga is no longer in Suwayomi: pick the source again in the series' chapters panel"
            continue
        row.title = row.title or manga.title
        if source not in will_have:
            row.status = NO_SOURCE
            need = need_of_source.get(source)
            row.message = (f"{need.name or need.pkg}: {need.state}" + (f" ({need.message})" if need.message else "")
                           if need is not None else "no repository of the host lists this source's extension")
            continue
        if dry_run:
            row.status = CARRIED
            continue
        if host_down:
            row.message = host_down
            continue
        _sleep(pause, first)
        first = False
        tell(done - 1, total, f"Carrying over {row.title or manga.title}...")
        try:
            got = host_client.register_manga(source, manga.url, manga.title)
        except NotFound as exc:
            row.status, row.message = NO_SOURCE, str(exc)
            continue
        except HostUnreachable as exc:
            host_down = f"the extension host stopped answering: {exc}"
            row.message = host_down
            continue
        except ExtensionHostError as exc:
            row.message = str(exc)
            continue
        row.host_manga_id = int(got["id"])
        carried = {"source_id": source, "manga_id": row.host_manga_id,
                   "manga_title": str(choice.get("manga_title") or manga.title), "how": row.how,
                   "carried_from": {"suwayomi_manga_id": suw_id, "at": utcnow()}}
        if settings.carry_series_choice(sid, carried):
            row.status = CARRIED
        else:                                   # chosen on the host meanwhile: that one is kept
            row.status = ALREADY
            row.host_manga_id = _int(SuwayomiSettings(db).extensions_series_choices().get(sid, {}).get("manga_id"))
    tell(total, total, "Done")
    report.seconds = time.monotonic() - started
    if unmapped:
        _log.info("Carry-over: no extension known for source(s) %s", ", ".join(unmapped))
    _log.info("Carry-over from Suwayomi%s: %s; extensions installed %d, failed %d; repositories added %d",
              " (dry run)" if dry_run else "", report.summary(),
              sum(1 for e in report.extensions if e.state == EXT_INSTALLED),
              sum(1 for e in report.extensions if e.state == EXT_FAILED), len(report.repos_added))
    return report


# --- from the stored connections (the CLI, Settings) ----------------------------------------------------------------

def clients_from_store(db, *, host_url: Optional[str] = None, timeout: float = 120.0):
    """(Suwayomi's client, the host's client) from MangaList's stored connections. Raises :class:`CarryOverError`
    when Suwayomi is not set up."""
    from ..suwayomi.client import SuwayomiClient
    from .client import ExtensionHostClient

    settings = SuwayomiSettings(db)
    conn = settings.suwayomi_connection()
    if conn is None:
        raise CarryOverError("Suwayomi is not set up (Settings > Connected services > Suwayomi): nothing to carry over")
    return SuwayomiClient(conn), ExtensionHostClient(host_url or settings.extension_host().url, timeout=timeout)


def has_suwayomi_choices(db) -> int:
    """How many series have a Suwayomi choice (with a manga) - quick, for showing the Carry over button."""
    return sum(1 for c in SuwayomiSettings(db).suwayomi_series_choices().values() if _int(c.get("manga_id")) is not None)


def not_yet_carried(db) -> int:
    """How many of them have no manga on the host yet."""
    settings = SuwayomiSettings(db)
    on_host = settings.extensions_series_choices()
    return sum(1 for sid, c in settings.suwayomi_series_choices().items()
               if _int(c.get("manga_id")) is not None and _int((on_host.get(sid) or {}).get("manga_id")) is None)


def run_from_store(db, *, install: bool = True, dry_run: bool = False, progress: Optional[Progress] = None,
                   host_url: Optional[str] = None, pause: float = DEFAULT_PAUSE) -> CarryReport:
    """:func:`carry_over` with the clients of the stored connections (closed afterwards)."""
    suwayomi, host = clients_from_store(db, host_url=host_url)
    try:
        return carry_over(db, suwayomi, host, install=install, dry_run=dry_run, progress=progress, pause=pause)
    finally:
        for c in (suwayomi, host):
            close = getattr(c, "close", None)
            if callable(close):
                close()


__all__ = ["ALREADY", "CARRIED", "FAILED", "NO_SOURCE", "STATUSES", "UNKNOWN", "CarryOverError", "CarryReport",
           "ExtensionNeed", "SeriesCarry", "carry_over", "clients_from_store", "has_suwayomi_choices",
           "not_yet_carried", "run_from_store"]
