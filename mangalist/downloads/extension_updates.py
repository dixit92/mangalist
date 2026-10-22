"""The extension update check (owner, 2026-10-22: "we must have the ability to update the extensions or add new ones";
chose **Update notice** and **Auto-update**, off by default). No Qt.

:func:`run_check` asks MangaList's extension host which installed extensions have a newer version in the repository
each was installed from (the host compares version codes; an extension whose repository was removed gets none). With
Auto-update on it then installs them one by one - the host checks each against its repository's signing key, as for a
manual update; a failure leaves the old version in place and is reported. An update never runs while a chapter of that
extension's sources is downloading: the host holds that source's queue, waits for the running chapter up to
:data:`WAIT_SECONDS`, and otherwise refuses (HTTP 409) - the update is then "waiting" and tried again at the next check.

The result is kept in the database's ``meta`` table (:data:`META_KEY`, JSON; no schema change): when it was checked, the
updates found, what Auto-update did (kept for :data:`KEEP_UPDATED_DAYS` days, so the notice still says it after a check
that found nothing), and what failed. The notices - a line on Settings > Connected services' Built-in extensions card and
one in the Download tab - read it (:func:`read_check`). The headless runner's ``extension-updates`` job runs the check on
its schedule (daily by default); the window checks once at start when the stored result is older than that
(:func:`is_due`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

_log = logging.getLogger(__name__)

META_KEY = "extensions.update_check"
WAIT_SECONDS = 60                   # how long an update waits for a chapter of its sources that is downloading
KEEP_UPDATED_DAYS = 7               # what Auto-update installed stays in the notice this long


@dataclass(frozen=True)
class PendingUpdate:
    pkg: str
    name: str
    version: str                    # installed
    new_version: str                # its repository's
    repo_name: str = ""


@dataclass(frozen=True)
class DoneUpdate:
    pkg: str
    name: str
    old_version: str
    new_version: str


@dataclass(frozen=True)
class FailedUpdate:
    pkg: str
    name: str
    error: str
    waiting: bool = False           # a chapter of its sources was downloading: tried again at the next check


@dataclass
class UpdateCheck:
    """One check's result as stored (and shown)."""

    checked_at: str = ""            # ISO 8601, UTC
    ok: bool = True
    error: str = ""                 # the host could not be asked
    auto: bool = False              # Auto-update was on
    installed: int = 0              # installed extensions seen
    pending: List[PendingUpdate] = field(default_factory=list)
    updated: List[DoneUpdate] = field(default_factory=list)
    updated_at: str = ""            # when Auto-update installed :attr:`updated`
    failed: List[FailedUpdate] = field(default_factory=list)

    # --- stored form -----------------------------------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: Optional[str]) -> Optional["UpdateCheck"]:
        try:
            d = json.loads(text) if text else None
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None

        def items(key: str, kind) -> list:
            out = []
            for item in d.get(key) or ():
                if isinstance(item, dict):
                    try:
                        out.append(kind(**{k: v for k, v in item.items() if k in kind.__dataclass_fields__}))
                    except TypeError:
                        continue
            return out

        return cls(checked_at=str(d.get("checked_at") or ""), ok=d.get("ok", True) is not False,
                   error=str(d.get("error") or ""), auto=bool(d.get("auto")), installed=int(d.get("installed") or 0),
                   pending=items("pending", PendingUpdate), updated=items("updated", DoneUpdate),
                   updated_at=str(d.get("updated_at") or ""), failed=items("failed", FailedUpdate))

    # --- reading -----------------------------------------------------------------------------------------------------

    @property
    def checked(self) -> Optional[datetime]:
        return _parse(self.checked_at)

    @property
    def count(self) -> int:
        return len(self.pending)

    @property
    def errors(self) -> List[FailedUpdate]:
        return [f for f in self.failed if not f.waiting]

    def summary(self) -> str:
        """One line for the log and the runner's record."""
        if not self.ok:
            return f"the extension host could not be asked: {self.error}"
        parts = []
        if self.updated and self.updated_at == self.checked_at:
            parts.append("updated automatically: " + ", ".join(f"{u.name} {u.old_version} -> {u.new_version}"
                                                               for u in self.updated))
        if self.pending:
            parts.append(f"{_plural(len(self.pending), 'update')} available: "
                         + ", ".join(f"{p.name} {p.version} -> {p.new_version}" for p in self.pending))
        for f in self.failed:
            parts.append(f"{f.name} {'waits' if f.waiting else 'not updated'}: {f.error}")
        if not parts:
            parts.append(f"{_plural(self.installed, 'installed extension')}, all up to date")
        return "; ".join(parts)

    def short_text(self) -> str:
        """The Download tab's line ('' when there is nothing to tell)."""
        bits = []
        if self.pending:
            bits.append(_plural(len(self.pending), "extension update"))
        if self.updated:
            bits.append(f"{_plural(len(self.updated), 'extension')} updated automatically")
        if self.errors:
            bits.append(f"{_plural(len(self.errors), 'extension')} could not be updated")
        return " · ".join(bits)

    def detail_text(self, now: Optional[datetime] = None) -> str:
        """The Built-in extensions card's line: what was found, done and failed, and when it was checked."""
        when = _when(self.checked, now)
        if not self.ok:
            return f"Extension updates: the host could not be asked ({when}): {self.error}"
        lines = []
        if self.pending:
            lines.append(f"{_plural(len(self.pending), 'extension update')}: "
                         + ", ".join(f"{p.name} {p.version} → {p.new_version}" for p in self.pending))
        if self.updated:
            lines.append(f"Updated automatically ({_when(_parse(self.updated_at), now)}): "
                         + ", ".join(f"{u.name} {u.new_version}" for u in self.updated))
        for f in self.failed:
            lines.append(f"{f.name}: {'waits for its download, tried again at the next check' if f.waiting else 'not updated'}"
                         f" ({f.error})")
        if not lines:
            lines.append(f"Extensions up to date ({_plural(self.installed, 'installed')})")
        return "\n".join(lines) + f"\nChecked {when}" + (" · Auto-update on" if self.auto else "")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _parse(text: str) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def _when(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    if when is None:
        return "never"
    local = when.astimezone()
    now = (now or datetime.now(timezone.utc)).astimezone()
    if local.date() == now.date():
        return f"today {local:%H:%M}"
    if local.date() == (now - timedelta(days=1)).date():
        return f"yesterday {local:%H:%M}"
    return f"{local:%Y-%m-%d %H:%M}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# --- the stored result ------------------------------------------------------------------------------------------------

def read_check(db: Any) -> Optional[UpdateCheck]:
    """The last check's result (None: never checked or unreadable)."""
    try:
        return UpdateCheck.from_json(db.get_meta(META_KEY))
    except Exception:  # noqa: BLE001 - a notice only
        _log.debug("Extension updates: the stored result could not be read", exc_info=True)
        return None


def save_check(db: Any, check: UpdateCheck) -> None:
    db.set_meta(META_KEY, check.to_json())


def is_due(db: Any, interval: Optional[timedelta], now: Optional[datetime] = None) -> bool:
    """Whether the stored result is older than *interval* (none stored: due; *interval* None - the schedule is off -:
    never)."""
    if interval is None:
        return False
    last = read_check(db)
    checked = last.checked if last is not None else None
    if checked is None:
        return True
    return (now or _now()) - checked >= interval


# --- the check --------------------------------------------------------------------------------------------------------

def _pending(installed: Sequence[Any]) -> List[PendingUpdate]:
    return [PendingUpdate(e.pkg, e.name, e.version, str(e.repo_version or ""), e.repo_name or "")
            for e in installed if getattr(e, "update_available", False)]


def run_check(db: Any, client: Any, *, auto: bool = False, wait_seconds: int = WAIT_SECONDS,
              now: Optional[datetime] = None, should_stop=lambda: False) -> UpdateCheck:
    """Ask the host (*client*: :class:`~mangalist.services.extensions.client.ExtensionHostClient`) for updates; with
    *auto*, install them one by one; store and log the result and return it. A host that cannot be asked is stored as
    such (``ok`` False) and its error raised again (:class:`~mangalist.services.extensions.client.ExtensionHostError`)."""
    from ..services.extensions.client import Busy, ExtensionHostError

    stamp = (now or _now()).isoformat()
    previous = read_check(db)
    check = UpdateCheck(checked_at=stamp, auto=bool(auto))
    try:
        installed = client.extensions()
    except ExtensionHostError as exc:
        check.ok, check.error = False, str(exc)
        if previous is not None:            # keep what is known; say the host could not be asked
            check.pending, check.updated, check.updated_at = previous.pending, previous.updated, previous.updated_at
        save_check(db, check)
        _log.warning("Extension updates: %s", check.summary())
        raise
    check.installed = len(installed)
    pending = _pending(installed)
    if auto and pending:
        for p in pending:
            if should_stop():
                break
            try:
                new = client.update(p.pkg, wait_seconds=wait_seconds)
            except Busy as exc:
                check.failed.append(FailedUpdate(p.pkg, p.name, str(exc), waiting=True))
                _log.info("Extension updates: %s waits: %s", p.name, exc)
                continue
            except ExtensionHostError as exc:
                check.failed.append(FailedUpdate(p.pkg, p.name, str(exc)))
                _log.warning("Extension updates: %s %s -> %s failed (the old version stays): %s", p.name, p.version,
                             p.new_version, exc)
                continue
            if new.version != p.version:
                check.updated.append(DoneUpdate(p.pkg, p.name, p.version, new.version))
                _log.info("Extension updates: %s %s -> %s installed", p.name, p.version, new.version)
            else:
                check.failed.append(FailedUpdate(p.pkg, p.name, "the host installed no newer version"))
        try:
            pending = _pending(client.extensions())
        except ExtensionHostError:
            done = {u.pkg for u in check.updated}
            pending = [p for p in pending if p.pkg not in done]
    check.pending = pending
    if check.updated:
        check.updated_at = stamp
    elif previous is not None and previous.updated:
        at = _parse(previous.updated_at)
        if at is not None and (now or _now()) - at < timedelta(days=KEEP_UPDATED_DAYS):
            current = {e.pkg for e in installed}
            check.updated = [u for u in previous.updated if u.pkg in current]
            check.updated_at = previous.updated_at if check.updated else ""
    save_check(db, check)
    _log.info("Extension updates: %s", check.summary())
    return check


def notice_payload(check: Optional[UpdateCheck]) -> Dict[str, Any]:
    """The runner's record extras for a check."""
    if check is None:
        return {}
    return {"installed": check.installed, "pending": [asdict(p) for p in check.pending],
            "updated": [asdict(u) for u in check.updated if check.updated_at == check.checked_at],
            "failed": [asdict(f) for f in check.failed], "auto": check.auto}
