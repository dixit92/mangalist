"""The extension host on demand while Suwayomi is chosen (transition audit C1, 2026-10-24). No Qt.

In the container the supervisor (:mod:`.supervisor`) runs the host while "Built-in extensions" is chosen for chapter
downloads. With Suwayomi chosen it used to stop it - so the Extensions page, Test connection, the repository list, Carry
over from Suwayomi (offered "before switching, or after") and Carry back could not work, and the owner could not prepare
the host before switching without changing the container.

Now a call that needs the host while Suwayomi is chosen asks for it first (:func:`ensure_running`): MangaList stores
"keep the host running until <now + 30 min>" (``meta`` :data:`META_UNTIL` - not a setting, which the window copies
around - refreshed by every such call), the
supervisor reads it at its next poll (every 15 s) and starts the host, and the call waits until the host answers (at most
:data:`START_WAIT_SECONDS`). Thirty minutes after the last use the supervisor stops it again. Where no supervisor runs
(a desktop, or ``MANGALIST_EXTENSION_HOST=0``) the call says so at once instead of waiting: the host must be started by
hand there.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Optional

_log = logging.getLogger(__name__)

META_UNTIL = "extensions.host_on_demand"           # ISO 8601 UTC: the supervisor keeps the host running until then
ON_DEMAND_MINUTES = 30
START_WAIT_SECONDS = 90.0
POLL_SECONDS = 2.0


class HostNotRunning(Exception):
    """The host is not running and cannot be started on demand here (the message says why and what to do)."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def request(store, minutes: float = ON_DEMAND_MINUTES, now: Optional[datetime] = None) -> None:
    """Ask the supervisor to keep the host running for *minutes* from *now* (never shortens a later request)."""
    until = (now or _now()) + timedelta(minutes=minutes)
    current = wanted_until(store)
    if current is None or current < until:
        store.set_meta(META_UNTIL, until.isoformat())


def wanted_until(store) -> Optional[datetime]:
    """Until when the host was asked for (None: never, or unreadable)."""
    try:
        value = store.get_meta(META_UNTIL)
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except (TypeError, ValueError):
        return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def is_wanted(store, now: Optional[datetime] = None) -> bool:
    when = wanted_until(store)
    return when is not None and when > (now or _now())


def supervised(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether a supervisor starts the host here: the container (it sets ``MANGALIST_EXTENSION_HOST``), not switched
    off."""
    from .supervisor import ENV_SWITCH, switch

    env = os.environ if env is None else env
    return ENV_SWITCH in env and switch(env) != "off"


def ensure_running(db, url: str, *, probe: Optional[Callable[[], object]] = None,
                   env: Optional[Mapping[str, str]] = None, wait: float = START_WAIT_SECONDS,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
    """Before a call to the host: with "Built-in extensions" chosen nothing to do (the supervisor runs it anyway).
    With Suwayomi chosen the host is asked for (see the module docstring) and waited for. Raises
    :class:`HostNotRunning` when it is not running and cannot be started here, or did not answer in time."""
    from ...store.downloads import SuwayomiSettings
    from .client import ExtensionHostClient, ExtensionHostError

    if SuwayomiSettings(db).builtin:
        return

    def default_probe() -> object:
        client = ExtensionHostClient(url, timeout=3.0)
        try:
            return client.health()
        finally:
            client.close()

    probe = probe or default_probe

    def up() -> bool:
        try:
            probe()
            return True
        except ExtensionHostError:
            return False

    if not supervised(env):
        if up():
            return
        from .supervisor import ENV_SWITCH

        env = os.environ if env is None else env
        if ENV_SWITCH in env:
            raise HostNotRunning("the extension host is switched off in this container (MANGALIST_EXTENSION_HOST=0)")
        raise HostNotRunning(f"the extension host is not running at {url}: Suwayomi is chosen for chapter downloads, "
                             "and outside the container the host is started by hand (see extension-host/README.md)")
    request(db)                               # also refreshes the request while the host already runs
    if up():
        return
    _log.info("Extension host: asked for on demand (Suwayomi is chosen); waiting up to %.0f s for it", wait)
    deadline = clock() + wait
    while clock() < deadline:
        sleep(POLL_SECONDS)
        if up():
            _log.info("Extension host: running on demand")
            return
    raise HostNotRunning(f"the extension host did not start within {wait:.0f} s (with Suwayomi chosen it starts on "
                         "demand; see the Extensions area of the log)")


__all__ = ["HostNotRunning", "META_UNTIL", "ON_DEMAND_MINUTES", "ensure_running", "is_wanted", "request",
           "supervised", "wanted_until"]
