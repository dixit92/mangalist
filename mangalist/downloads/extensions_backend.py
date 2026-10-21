"""The GUI backend's "Built-in extensions" part (prototype/mihon-mangalist): the chapter backend choice, MangaList's
own extension host (address, folders, options, Test connection) and its extensions (repository, install / update /
uninstall, source settings). Mixed into :class:`mangalist.downloads.adapter.Backend`. No Qt.

Every call that asks the host may block (the GUI calls them off the UI thread); a failure the owner should read is a
:class:`~mangalist.gui.downloads_backend.BackendError` with the host's own message.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, List, Optional, Sequence, Tuple

from ..gui.downloads_backend import BackendError, ExtensionHostCheck
from ..services.extensions.client import (
    Extension,
    ExtensionHostClient,
    ExtensionHostError,
    RepoExtension,
    SourcePreference,
)
from ..services.suwayomi import SuwayomiError
from ..store.downloads import ExtensionHostConfig
from .contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKENDS, SuwayomiConnection

_log = logging.getLogger(__name__)

ENV_REPO = "MANGALIST_EXTENSION_REPO"
ENV_FLARESOLVERR = "MANGALIST_FLARESOLVERR_URL"


def repo_url(config: ExtensionHostConfig) -> str:
    """The repository MangaList installs from: the setting, else ``MANGALIST_EXTENSION_REPO``, else '' (the host's
    default, keiyoushi's)."""
    return config.repo_url or os.environ.get(ENV_REPO, "").strip()


def flaresolverr_url(config: ExtensionHostConfig) -> str:
    return config.flaresolverr_url or os.environ.get(ENV_FLARESOLVERR, "").strip()


def version_key(version: str) -> Tuple:
    """``'1.4.61'`` -> ``(1, 4, 61)``: extension versions compare by their numbers."""
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


@dataclass(frozen=True)
class ExtensionRow:
    """One extension as the Extensions page lists it: the repository's entry and what is installed of it."""

    pkg: str
    name: str
    lang: str
    nsfw: bool = False
    repo_version: str = ""              # '' when the repository no longer lists it
    installed_version: str = ""         # '' when not installed
    sources: Tuple[Tuple[str, str, str], ...] = ()      # (id, name, lang)

    @property
    def installed(self) -> bool:
        return bool(self.installed_version)

    @property
    def has_update(self) -> bool:
        return (self.installed and bool(self.repo_version)
                and version_key(self.repo_version) > version_key(self.installed_version))


def extension_rows(repo: Sequence[RepoExtension], installed: Sequence[Extension]) -> List[ExtensionRow]:
    """The repository's extensions joined with the installed ones (an installed one the repository dropped stays
    listed), installed first, then by name."""
    have = {e.pkg: e for e in installed}
    rows = []
    for r in repo:
        mine = have.pop(r.pkg, None)
        sources = tuple((s.id, s.name, s.lang) for s in (mine.sources if mine else r.sources))
        rows.append(ExtensionRow(pkg=r.pkg, name=r.name, lang=r.lang, nsfw=r.nsfw, repo_version=r.version,
                                 installed_version=mine.version if mine else "", sources=sources))
    for e in have.values():
        rows.append(ExtensionRow(pkg=e.pkg, name=e.name, lang="", nsfw=bool(e.nsfw), installed_version=e.version,
                                 sources=tuple((s.id, s.name, s.lang) for s in e.sources)))
    rows.sort(key=lambda r: (not r.installed, r.name.casefold(), r.lang))
    return rows


class ExtensionsBackendMixin:
    """See the module docstring. Needs ``self.suwayomi`` (:class:`~mangalist.store.downloads.SuwayomiSettings`) and
    ``self.chapter_ledger``; ``self._host_client_factory`` (tests) builds the host client from an address."""

    _host_client_factory: Optional[Callable[[str], Any]] = None

    # --- the backend choice -------------------------------------------------------------------------------------

    def chapter_backend(self) -> str:
        return self.suwayomi.backend()

    def set_chapter_backend(self, backend: str) -> None:
        if backend not in CHAPTER_BACKENDS:
            raise BackendError(f"unknown chapter backend {backend!r}")
        if backend == self.suwayomi.backend():
            return
        busy = [r for r in self.chapter_ledger.active()]
        if busy:
            now = "the extension host" if self.suwayomi.builtin else "Suwayomi"
            raise BackendError(f"{len(busy)} chapter download(s) are still in progress with {now}: switch once they "
                               "are filed (or cancel them in the Download tab's In progress list)")
        self.suwayomi.set_backend(backend)
        _log.info("Chapter downloads: now through %s", "the built-in extensions" if backend == CHAPTER_BACKEND_EXTENSIONS
                  else "Suwayomi")

    # --- the host -----------------------------------------------------------------------------------------------

    def load_extension_host(self) -> ExtensionHostConfig:
        return self.suwayomi.extension_host()

    def save_extension_host(self, config: ExtensionHostConfig) -> None:
        self.suwayomi.save_extension_host(self._checked_host(config))

    def _checked_host(self, config: ExtensionHostConfig) -> ExtensionHostConfig:
        from ..services.extensions.compat import normalize_host_url

        try:
            url = normalize_host_url(config.url)
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        for name, value in (("repository", config.repo_url), ("FlareSolverr", config.flaresolverr_url)):
            if value and not re.match(r"^https?://", value.strip(), re.I):
                raise BackendError(f"the {name} address must start with http:// or https://")
        return replace(config, url=url)

    def _host(self, config: Optional[ExtensionHostConfig] = None):
        url = (config or self.suwayomi.extension_host()).url
        factory = self._host_client_factory
        return factory(url) if factory is not None else ExtensionHostClient(url, timeout=300.0)

    def test_extension_host(self, config: Optional[ExtensionHostConfig] = None) -> ExtensionHostCheck:
        """The host's version, its extensions and sources, and the folders; learns the host's downloads folder."""
        from . import chapter_backend

        config = self._checked_host(config or self.suwayomi.extension_host())
        conn = SuwayomiConnection(base_url=config.url, backend=CHAPTER_BACKEND_EXTENSIONS)
        compat = (self._chapter_client_factory or chapter_backend.client_from_connection)(conn)
        host = self._host(config)
        try:
            info = compat.server_settings()
            installed = host.extensions()
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
        host = self._host()
        try:
            return fn(host)
        except ExtensionHostError as exc:
            _log.warning("Extensions: %s failed: %s", what, exc)
            raise BackendError(str(exc)) from None
        finally:
            _close(host)

    def extension_repo(self) -> List[ExtensionRow]:
        """The repository's extensions (the configured repository, else the host's default) with what is installed."""
        config = self.suwayomi.extension_host()
        url = repo_url(config) or None
        return self._host_call("reading the repository",
                               lambda h: extension_rows(h.repo(url), h.extensions()))

    def installed_extensions(self) -> List[Extension]:
        return self._host_call("listing the extensions", lambda h: h.extensions())

    def install_extension(self, pkg: str) -> Extension:
        url = repo_url(self.suwayomi.extension_host()) or None
        ext = self._host_call(f"installing {pkg}", lambda h: h.install(pkg, repo=url))
        _log.info("Extensions: installed %s %s (%d source(s))", ext.name, ext.version, len(ext.sources))
        return ext

    def update_extension(self, pkg: str) -> Extension:
        url = repo_url(self.suwayomi.extension_host()) or None
        ext = self._host_call(f"updating {pkg}", lambda h: h.update(pkg, repo=url))
        _log.info("Extensions: updated %s to %s", ext.name, ext.version)
        return ext

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


def _close(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
