"""Why a run failed, in words an alert can carry.

The nightly run stops on the first error it cannot work around. ``explain`` turns that
exception into one sentence: which site and request it was, what kind of failure (no answer
in time, no connection, a dropped connection, bot protection, a failed data test, or an
error in Undercurrent's own code) and how often the request was tried. ``pipeline.run_day``
records it with the run (``state/runs.failure``); the alert step adds ``reachable_now``: one
request to the same site, which tells a short outage that is over from one still going on.
"""

from __future__ import annotations

import traceback
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx

from undercurrent.fetch import REQUEST_NOTE, RETRY_PAUSES_S, USER_AGENT, SourceBlockedError

# The run's sources, for a reason a person reads without knowing the code.
_SITES = {
    "www.gpw.pl": "gpw.pl (the exchange's site)",
    "gpw.pl": "gpw.pl (the exchange's site)",
    "newconnect.pl": "newconnect.pl (NewConnect's site)",
    "www.newconnect.pl": "newconnect.pl (NewConnect's site)",
    "www.bankier.pl": "bankier.pl",
    "www.parkiet.com": "parkiet.com",
    "www.biznesradar.pl": "biznesradar.pl",
    "api.telegram.org": "Telegram",
}
ATTEMPTS = len(RETRY_PAUSES_S) + 1  # PoliteClient retries a timeout or dropped connection
_RETRY_MINUTES = round(sum(RETRY_PAUSES_S) / 60)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: BaseException | None = exc
    while seen is not None:
        yield seen
        seen = seen.__cause__ or seen.__context__


def _request(exc: BaseException) -> httpx.Request | None:
    """The request that failed: the note ``PoliteClient`` adds, else what httpx attached."""
    for e in _chain(exc):
        for note in getattr(e, "__notes__", ()):
            if note.startswith(REQUEST_NOTE):
                method, _, url = note.removeprefix(REQUEST_NOTE).partition(" ")
                return httpx.Request(method, url)
    for e in _chain(exc):
        if isinstance(e, httpx.RequestError):
            try:
                return e.request
            except RuntimeError:  # raised without a request attached: look further down
                continue
    return None


def site(url: str) -> str:
    """A source named for a reader: ``gpw.pl (the exchange's site)``."""
    host = urlsplit(url).hostname or url
    return _SITES.get(host, host)


def _where(req: httpx.Request | None) -> str:
    if req is None:
        return "a source"
    return f"{site(str(req.url))} ({req.method} {req.url.host}{req.url.path})"


def _own_frame(exc: BaseException) -> str | None:
    """``ingest.py:395`` for the innermost frame of Undercurrent's own code."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if "/src/undercurrent/" in frame.filename.replace("\\", "/"):
            return f"{frame.filename.replace(chr(92), '/').rsplit('/', 1)[-1]}:{frame.lineno}"
    return None


def explain(exc: BaseException) -> str:
    """One sentence on why the run stopped, for the run's record and the alert."""
    req = _request(exc)
    where = _where(req)
    tries = f"tried {ATTEMPTS} times, {_RETRY_MINUTES} minutes of pauses apart"
    kinds = {type(e) for e in _chain(exc)}
    if any(issubclass(k, httpx.TimeoutException) for k in kinds):
        kind = next(k.__name__ for k in kinds if issubclass(k, httpx.TimeoutException))
        return (
            f"{where} did not answer in time ({kind}; {tries}). The site was down or "
            "overloaded: an outage on its side, not a fault in Undercurrent."
        )
    if any(issubclass(k, httpx.ConnectError) for k in kinds):
        return (
            f"no connection to {where} could be opened ({tries}): the site, its DNS or the "
            "network was down. An outage outside Undercurrent."
        )
    if any(issubclass(k, httpx.TransportError) for k in kinds):
        kind = next(k.__name__ for k in kinds if issubclass(k, httpx.TransportError))
        return (
            f"{where} dropped the connection ({kind}; {tries}): an outage or a network "
            "fault outside Undercurrent."
        )
    if any(issubclass(k, SourceBlockedError) for k in kinds):
        return (
            f"{exc} - the site answered with a bot-protection page instead of data. It "
            "changed how it treats automated clients; that needs a look, not a retry."
        )
    if type(exc).__name__ == "DbtError":
        return f"a data test failed (dbt): {exc}. The new data breaks an invariant; see the log."
    frame = _own_frame(exc)
    at = f" at {frame}" if frame else ""
    return f"an error in Undercurrent's own code{at}: {type(exc).__name__}: {exc}"


def is_outage(reason: str | None) -> bool:
    """Whether a recorded reason is a source being unreachable (``explain``'s first three)."""
    return bool(reason) and any(
        s in str(reason) for s in ("did not answer", "no connection", "dropped the connection")
    )


def reachable_now(reason: str | None, timeout_s: float = 20.0) -> str | None:
    """For an outage: does the site answer now? One request to its home page."""
    if not is_outage(reason):
        return None
    host = next((h for h in _SITES if f"{h}/" in str(reason) or f" {h}" in str(reason)), None)
    if host is None:
        return None
    try:
        r = httpx.get(
            f"https://{host}/",
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return f"{site(host)} still does not answer now ({type(exc).__name__})."
    if r.status_code >= 500:
        return f"{site(host)} still answers with errors now (HTTP {r.status_code})."
    return f"{site(host)} answers again now: the outage was short."
