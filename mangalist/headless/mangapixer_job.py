"""The ``mangapixer-sync`` job for the headless runner (not registered here - the integrator adds it to
:func:`mangalist.headless.jobs.build_registry`, e.g.::

    from .mangapixer_job import JOB_NAME, make_mangapixer_sync
    Job(JOB_NAME, make_mangapixer_sync(), settings.rescan_schedule, enabled=..., description=DESCRIPTION)

It runs one incremental sync of every MangaPixer library MangaList uses (a full sync the first time or
when MangaPixer asks for one), then refreshes the automatic root mappings. Skipped when no MangaPixer
source is configured, and while MangaPixer's last answer was "token refused" (HTTP 401 is never retried
automatically; a new token in the settings, or "Sync now" in the GUI, lifts that stop). Good to run at
the start of each batch, before the rescan's states are computed.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .jobs import Cancelled, JobContext, JobFunc, JobResult

_log = logging.getLogger(__name__)

JOB_NAME = "mangapixer-sync"
DESCRIPTION = "Sync the MangaPixer source (incremental)"


def make_mangapixer_sync(open_cache: Optional[Callable[[], object]] = None, client_factory=None) -> JobFunc:
    """The job function. *open_cache* / *client_factory* are for tests (default: the data folder's
    database and a client built from its stored connection)."""

    def mangapixer_sync(ctx: JobContext) -> JobResult:
        from ..services.mangapixer import open_cache as _open
        from ..services.mangapixer.sync import sync_all

        cache = (open_cache or _open)()
        client = client_factory(cache) if client_factory is not None else None
        result = sync_all(cache, client=client, should_stop=lambda: ctx.stop_requested, manual=False)
        if ctx.stop_requested:
            raise Cancelled()
        extra = result.summary()
        extra["mappings"] = {str(rid): {"library": m.library_id, "prefix": m.prefix, "manual": m.manual,
                                        "matched": m.matched, "unmatched": m.unmatched}
                             for rid, m in result.mappings.items()}
        return JobResult(result.status, result.message, extra)

    return mangapixer_sync
