"""The GUI backend's "Built-in extensions" part (prototype/mihon-mangalist): the chapter backend choice, MangaList's
own extension host (address, folders, options, Test connection), its repository list (add - checked by the host first -
and remove), its extensions (install from a repository / update / uninstall, source settings) and the extension update
check (:mod:`.extension_updates`: the notice, Auto-update). Mixed into :class:`mangalist.downloads.adapter.Backend`.
No Qt.

Every call that asks the host may block (the GUI calls them off the UI thread); a failure the owner should read is a
:class:`~mangalist.gui.downloads_backend.BackendError` with the host's own message.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from ..gui.downloads_backend import BackendError, ExtensionHostCheck
from ..services.extensions.client import (
    Extension,
    ExtensionHostClient,
    ExtensionHostError,
    RepoExtension,
    Repository,
    SourcePreference,
)
from ..services.suwayomi import SuwayomiError
from ..store.downloads import ExtensionHostConfig
from .contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKENDS, SuwayomiConnection

_log = logging.getLogger(__name__)

ENV_REPO = "MANGALIST_EXTENSION_REPO"
ENV_FLARESOLVERR = "MANGALIST_FLARESOLVERR_URL"


def repo_urls(config: ExtensionHostConfig, env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """The repositories the host is started with (``--repo`` each): the list as the owner last left it (Settings), else
    an older version's single repository setting, else ``MANGALIST_EXTENSION_REPO`` (several separated by spaces,
    commas or semicolons), else none (the host's own list; on its first start keiyoushi's)."""
    if config.repos is not None:
        return tuple(config.repos)
    if config.repo_url:
        return (config.repo_url,)
    raw = (os.environ if env is None else env).get(ENV_REPO, "")
    return tuple(dict.fromkeys(u for u in re.split(r"[\s,;]+", raw) if u))


def repo_url(config: ExtensionHostConfig, env: Optional[Mapping[str, str]] = None) -> str:
    """The first of :func:`repo_urls` ('' for none)."""
    urls = repo_urls(config, env)
    return urls[0] if urls else ""


def flaresolverr_url(config: ExtensionHostConfig, env: Optional[Mapping[str, str]] = None) -> str:
    """The FlareSolverr the host uses: the setting, else ``MANGALIST_FLARESOLVERR_URL``, else '' (none)."""
    return config.flaresolverr_url or (os.environ if env is None else env).get(ENV_FLARESOLVERR, "").strip()


def version_key(version: str) -> Tuple:
    """``'1.4.61'`` -> ``(1, 4, 61)``: extension versions compare by their numbers."""
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


@dataclass(frozen=True)
class ExtensionRow:
    """One extension of one repository as the Extensions page lists it, and what is installed of it. The same package
    in two repositories is two rows (an extension installed from one of them is installed in that row only; the other
    says so in :attr:`installed_from`)."""

    pkg: str
    name: str
    lang: str
    nsfw: bool = False
    repo_version: str = ""              # '' when the repository no longer lists it
    installed_version: str = ""         # '' when not installed (from this repository)
    sources: Tuple[Tuple[str, str, str], ...] = ()      # (id, name, lang)
    repo: str = ""                      # the repository's index URL ('' from a host that names none)
    repo_name: str = ""
    installed_from: str = ""            # installed from ANOTHER repository: its name (not installable here)
    repo_listed: bool = True            # False: installed from a repository no longer in the list (no updates)

    @property
    def installed(self) -> bool:
        return bool(self.installed_version)

    @property
    def has_update(self) -> bool:
        return (self.installed and bool(self.repo_version)
                and version_key(self.repo_version) > version_key(self.installed_version))


def _repo_label(url: str, name: str = "") -> str:
    if name:
        return name
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or url


def extension_rows(repo: Sequence[RepoExtension], installed: Sequence[Extension]) -> List[ExtensionRow]:
    """Every repository's extensions joined with the installed ones - each installed extension with the repository it
    came from (an installed one its repository dropped, or whose repository was removed, stays listed), installed
    first, then by name and repository."""
    have = {e.pkg: e for e in installed}
    placed: set = set()
    rows = []
    for r in repo:
        mine = have.get(r.pkg)
        same = mine is not None and r.pkg not in placed and (not mine.repo or not r.repo or mine.repo == r.repo)
        if same:
            placed.add(r.pkg)
        sources = tuple((s.id, s.name, s.lang) for s in (mine.sources if same else r.sources))
        rows.append(ExtensionRow(pkg=r.pkg, name=r.name, lang=r.lang, nsfw=r.nsfw, repo_version=r.version,
                                 installed_version=mine.version if same else "", sources=sources, repo=r.repo,
                                 repo_name=_repo_label(r.repo, r.repo_name) if r.repo else "",
                                 installed_from=_repo_label(mine.repo, mine.repo_name)
                                 if mine is not None and not same and mine.repo else "",
                                 repo_listed=mine.repo_listed if same else True))
    for e in have.values():
        if e.pkg in placed:
            continue
        rows.append(ExtensionRow(pkg=e.pkg, name=e.name, lang="", nsfw=bool(e.nsfw), installed_version=e.version,
                                 sources=tuple((s.id, s.name, s.lang) for s in e.sources), repo=e.repo,
                                 repo_name=_repo_label(e.repo, e.repo_name) if e.repo else "",
                                 repo_listed=e.repo_listed))
    rows.sort(key=lambda r: (not r.installed, r.name.casefold(), r.lang, r.repo_name.casefold()))
    return rows


class ExtensionsBackendMixin:
    """See the module docstring. Needs ``self.suwayomi`` (:class:`~mangalist.store.downloads.SuwayomiSettings`) and
    ``self.chapter_ledger``; ``self._host_client_factory`` (tests) builds the host client from an address."""

    _host_client_factory: Optional[Callable[[str], Any]] = None

    # --- the backend choice -------------------------------------------------------------------------------------

    def chapter_backend(self) -> str:
        return self.suwayomi.backend()

    def set_chapter_backend(self, backend: str) -> None:
        """Switch the program that downloads chapters. Refused while a chapter download of the current program is still
        on its way (sent, downloaded: its program must finish it - Cancel is offered for every such row, transition audit
        B1), and while a find pass or a chapter check runs (it holds the current program's client: B5). A filed record
        does not hold a switch: its program deletes its copy whichever is chosen (C2)."""
        from . import chapter_backend
        from .contracts import DownloadStatus

        if backend not in CHAPTER_BACKENDS:
            raise BackendError(f"unknown chapter backend {backend!r}")
        current = self.suwayomi.backend()
        if backend == current:
            return
        now = chapter_backend.name_of_backend(current)
        busy = [r for r in self.chapter_ledger.active() if r.status in (DownloadStatus.SENT, DownloadStatus.DOWNLOADED)
                and r.backend in (current, "")]
        if busy:
            raise BackendError(f"{len(busy)} chapter download(s) are still on their way with {now}: switch once they "
                               "are filed, or cancel them (Download tab > In progress: right-click the row > Cancel "
                               "download...)")
        running = passes_running(self.suwayomi.store)
        if running:
            raise BackendError(f"{running} is running with {now}: switch when it has finished")
        self.suwayomi.set_backend(backend)
        _log.info("Chapter downloads: now through %s", "the built-in extensions" if backend == CHAPTER_BACKEND_EXTENSIONS
                  else "Suwayomi")

    # --- the host -----------------------------------------------------------------------------------------------

    def load_extension_host(self) -> ExtensionHostConfig:
        return self.suwayomi.extension_host()

    def save_extension_host(self, config: ExtensionHostConfig) -> None:
        """Save the host's form. The repository list is not part of it (it changes through
        :meth:`add_extension_repository` / :meth:`remove_extension_repository`): the stored one is kept."""
        current = self.suwayomi.extension_host()
        self.suwayomi.save_extension_host(replace(self._checked_host(config), repos=current.repos))

    def _checked_host(self, config: ExtensionHostConfig) -> ExtensionHostConfig:
        from ..services.extensions.compat import normalize_host_url

        try:
            url = normalize_host_url(config.url)
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        for name, value in (("FlareSolverr", config.flaresolverr_url),):
            if value and not re.match(r"^https?://", value.strip(), re.I):
                raise BackendError(f"the {name} address must start with http:// or https://")
        return replace(config, url=url)

    #: Tests: the host's liveness probe for :meth:`_host_up` (None: a real ``GET /health``).
    _host_probe: Optional[Callable[[], Any]] = None

    def _host_up(self, config: Optional[ExtensionHostConfig] = None) -> None:
        """Before a call to the host: with Suwayomi chosen, ask for the host on demand and wait for it (transition audit
        C1: the Extensions page, Test connection and the carry-over work before switching); a host that cannot run here
        is a :class:`BackendError` saying why."""
        from ..services.extensions.on_demand import HostNotRunning, ensure_running

        if self.suwayomi.builtin or (self._host_client_factory is not None and self._host_probe is None):
            return
        try:
            ensure_running(self.suwayomi.store, (config or self.suwayomi.extension_host()).url, probe=self._host_probe)
        except HostNotRunning as exc:
            text = str(exc)
            raise BackendError(text[:1].upper() + text[1:]) from None

    def _host(self, config: Optional[ExtensionHostConfig] = None):
        url = (config or self.suwayomi.extension_host()).url
        factory = self._host_client_factory
        return factory(url) if factory is not None else ExtensionHostClient(url, timeout=300.0)

    def test_extension_host(self, config: Optional[ExtensionHostConfig] = None) -> ExtensionHostCheck:
        """The host's version, its extensions and sources, and the folders; learns the host's downloads folder."""
        from . import chapter_backend

        config = self._checked_host(config or self.suwayomi.extension_host())
        self._host_up(config)
        conn = SuwayomiConnection(base_url=config.url, backend=CHAPTER_BACKEND_EXTENSIONS)
        compat = (self._chapter_client_factory or chapter_backend.client_from_connection)(conn)
        host = self._host(config)
        try:
            info = compat.server_settings()
            installed = host.extensions()
            from . import host_identity

            host_identity.check(self.suwayomi.store, compat)
        except (SuwayomiError, ExtensionHostError) as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(compat)
            _close(host)
        downloads_path = str(info.get("downloads_path") or "")
        if downloads_path and downloads_path != config.downloads_path:
            self.suwayomi.learn_downloads_path(downloads_path)
        files_from = config.download_dir or downloads_path
        found = os.path.isdir(files_from) if files_from else False
        return ExtensionHostCheck(version=str(info.get("version") or "?"),
                                  sources=sum(len(e.sources) for e in installed), extensions=len(installed),
                                  downloads_path=downloads_path, files_from=files_from, folder_found=found,
                                  flaresolverr=bool(info.get("flaresolverr")),
                                  flaresolverr_url=str(info.get("flaresolverr_url") or ""))

    # --- the extensions -----------------------------------------------------------------------------------------

    def _host_call(self, what: str, fn: Callable[[Any], Any]) -> Any:
        self._host_up()
        host = self._host()
        try:
            return fn(host)
        except ExtensionHostError as exc:
            _log.warning("Extensions: %s failed: %s", what, exc)
            raise BackendError(str(exc)) from None
        finally:
            _close(host)

    def extension_repo(self) -> List[ExtensionRow]:
        """Every repository's extensions (the host's list) with what is installed, each with its repository."""
        return self._host_call("reading the repositories", lambda h: extension_rows(h.repo(), h.extensions()))

    def installed_extensions(self) -> List[Extension]:
        return self._host_call("listing the extensions", lambda h: h.extensions())

    def install_extension(self, pkg: str, repo: Optional[str] = None) -> Extension:
        """Install *pkg* from *repo* (one of the host's repositories; None: the first that lists it). The host checks
        it against that repository's own signing key."""
        ext = self._host_call(f"installing {pkg}", lambda h: h.install(pkg, repo=repo or None))
        _log.info("Extensions: installed %s %s from %s (%d source(s))", ext.name, ext.version,
                  ext.repo_name or ext.repo or "the repository", len(ext.sources))
        return ext

    def update_extension(self, pkg: str, wait_seconds: int = 10) -> Extension:
        """Update *pkg* from the repository it was installed from (the host waits up to *wait_seconds* for a chapter
        of its sources that is downloading, then refuses: try again later)."""
        ext = self._host_call(f"updating {pkg}", lambda h: h.update(pkg, wait_seconds=wait_seconds))
        _log.info("Extensions: updated %s to %s", ext.name, ext.version)
        return ext

    # --- the repositories ---------------------------------------------------------------------------------------

    def extension_repositories(self) -> List[Repository]:
        """The host's repository list, each with its name, signing key, extension count and last fetch."""
        return self._host_call("reading the repository list", lambda h: h.repos())

    def _remember_repos(self, repos: Sequence[Repository]) -> None:
        config = self.suwayomi.extension_host()
        self.suwayomi.save_extension_host(replace(config, repos=tuple(r.url for r in repos)))

    def add_extension_repository(self, url: str) -> Repository:
        """Add a repository by its index URL: the host fetches and checks it first (not an extension index, or no
        signing key: refused with the host's reason); then the list is remembered for the host's next start."""
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            raise BackendError("a repository address starts with http:// or https://")

        def add(h):
            added = h.add_repo(url)
            return added, h.repos()

        added, repos = self._host_call(f"adding the repository {url}", add)
        self._remember_repos(repos)
        _log.info("Extensions: repository added: %s (%s), %s extension(s)", added.name, added.url,
                  "?" if added.extensions is None else added.extensions)
        return added

    def remove_extension_repository(self, url: str) -> Repository:
        """Remove a repository; the extensions installed from it stay installed (and get no updates)."""

        def remove(h):
            gone = h.remove_repo(url)
            return gone, h.repos()

        gone, repos = self._host_call(f"removing the repository {url}", remove)
        self._remember_repos(repos)
        _log.info("Extensions: repository removed: %s (%s)", gone.name, gone.url)
        return gone

    # --- updates: the notice and Auto-update ---------------------------------------------------------------------

    def extension_auto_update(self) -> bool:
        return self.suwayomi.extension_auto_update()

    def set_extension_auto_update(self, on: bool) -> None:
        if bool(on) != self.suwayomi.extension_auto_update():
            self.suwayomi.set_extension_auto_update(on)
            _log.info("Extensions: auto-update %s", "on" if on else "off")

    def extension_update_notice(self):
        """The last update check's result (:class:`~.extension_updates.UpdateCheck`), or None - also while
        "Built-in extensions" is not chosen. A quick database read."""
        from . import extension_updates

        if not self.suwayomi.builtin:
            return None
        return extension_updates.read_check(self.suwayomi.store)

    def extension_updates_due(self) -> bool:
        """Whether the window should check now: Built-in extensions chosen and the stored result older than the
        schedule's interval (none stored: due; the schedule off: never)."""
        from ..headless.extension_updates_job import schedule_interval
        from . import extension_updates

        if not self.suwayomi.builtin:
            return False
        return extension_updates.is_due(self.suwayomi.store, schedule_interval(self.suwayomi.store))

    def check_extension_updates(self, auto: Optional[bool] = None):
        """Ask the host for updates now and store the result; with Auto-update on (*auto* None: the setting) install
        them. Blocks."""
        from . import extension_updates

        host = self._host()
        try:
            return extension_updates.run_check(self.suwayomi.store, host,
                                               auto=self.suwayomi.extension_auto_update() if auto is None else auto)
        except ExtensionHostError as exc:
            raise BackendError(str(exc)) from None
        finally:
            _close(host)

    def uninstall_extension(self, pkg: str) -> None:
        self._host_call(f"removing {pkg}", lambda h: h.uninstall(pkg))
        _log.info("Extensions: removed %s", pkg)

    def source_preferences(self, source_id: str) -> List[SourcePreference]:
        return self._host_call(f"reading source {source_id}'s settings", lambda h: h.preferences(source_id))

    def set_source_preference(self, source_id: str, pref: SourcePreference, value: Any) -> List[SourcePreference]:
        out = self._host_call(f"changing source {source_id}'s {pref.key}",
                              lambda h: h.set_preference(source_id, pref, value))
        _log.info("Extensions: source %s: %s set", source_id, pref.key)
        return out


#: A chapter check the headless runner started longer ago than this is not counted as running (a crashed runner).
CHECK_STALE_HOURS = 2


def passes_running(db) -> str:
    """What holds a chapter program's client now - "a find pass" or "a chapter check" (in this process, or the
    container's runner: its find run not finished, its state file's running job) - else ''."""
    import json
    from datetime import datetime, timedelta, timezone

    from . import chapter_check, finder
    from ..store.findings import FindingStore

    if finder._PASS.locked() or finder._busy_elsewhere(FindingStore(db)):
        return "a find pass"
    if chapter_check._CHECK.locked():
        return "a chapter check"
    try:
        from .. import paths
        from ..headless.chapter_check_job import JOB_NAME
        from ..headless.state import STATE_NAME

        data = json.loads((paths.data_dir() / STATE_NAME).read_text(encoding="utf-8"))
        since = datetime.fromisoformat(str(data["jobs"][JOB_NAME]["running_since"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return "a chapter check" if datetime.now(timezone.utc) - since < timedelta(hours=CHECK_STALE_HOURS) else ""


def _close(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
