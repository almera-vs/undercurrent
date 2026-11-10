"""Polite HTTP client shared by every ingest step.

Rules enforced here (see README → Design principles):

* at most one request per second per process, plus random jitter;
* a fixed, honest User-Agent that names the project (never a browser impersonation);
* conditional requests (``If-None-Match`` / ``If-Modified-Since``) against an on-disk cache;
* WAF / bot-protection pages are detected and raised as :class:`SourceBlockedError`.
  We never try to get around them; the caller records the block and moves on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "undercurrent/0.1 (+https://github.com/almera-vs/undercurrent)"
MIN_INTERVAL_S = 1.0
MAX_JITTER_S = 0.5
# A dropped connection or timeout is retried after these pauses (seconds) before giving up.
RETRY_PAUSES_S = (5.0, 30.0, 120.0)
DEFAULT_CACHE_DIR = Path(".cache/http")

# Markers of bot-protection interstitials seen on the official sites.
_BLOCK_MARKERS: tuple[bytes, ...] = (
    b"Request Rejected",  # F5 BIG-IP ASM on gpw.pl / newconnect.pl
    b"_Incapsula_Resource",  # Imperva on espiebi.pap.pl
)


class SourceBlockedError(RuntimeError):
    """Raised when a source answers with a bot-protection page instead of content."""


@dataclass(frozen=True)
class FetchResult:
    """One HTTP response body plus the metadata we persist alongside facts."""

    url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool
    fetched_at: float

    @property
    def sha256(self) -> str:
        """SHA-256 of the body, used as ``content_sha256`` on fact rows."""
        return hashlib.sha256(self.content).hexdigest()

    def text(self, encoding: str = "utf-8") -> str:
        """Decode the body (GPW serves UTF-8)."""
        return self.content.decode(encoding, errors="replace")


def is_block_page(content: bytes) -> bool:
    """Return True when ``content`` looks like a WAF / bot-challenge page."""
    head = content[:8192]
    return any(marker in head for marker in _BLOCK_MARKERS)


def _cache_key(method: str, url: str, data: dict[str, Any] | list[tuple[str, str]] | None) -> str:
    payload = json.dumps([method, url, data], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PoliteClient:
    """Rate-limited, caching HTTP client.

    ``max_age_s`` controls how long a cached body is served without contacting the
    server at all. After that the client revalidates with conditional headers.
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        *,
        min_interval_s: float = MIN_INTERVAL_S,
        max_jitter_s: float = MAX_JITTER_S,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.min_interval_s = min_interval_s
        self.max_jitter_s = max_jitter_s
        self._sleep = sleep
        self._last_request = 0.0
        self._rng = random.Random()
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.5"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.min_interval_s + self._rng.uniform(0, self.max_jitter_s)
        elapsed = time.monotonic() - self._last_request
        if elapsed < wait:
            self._sleep(wait - elapsed)
        self._last_request = time.monotonic()

    def _send(
        self, method: str, url: str, content: bytes | None, headers: dict[str, str]
    ) -> httpx.Response:
        """One request, retried on transport errors (timeouts, resets), never on HTTP status."""
        for pause in (*RETRY_PAUSES_S, None):
            try:
                return self._client.request(method, url, content=content, headers=headers)
            except httpx.TransportError as exc:
                if pause is None:
                    raise
                log.warning("%s %s: %s; retrying in %.0fs", method, url, type(exc).__name__, pause)
                self._sleep(pause)
        raise AssertionError("unreachable")

    def _paths(self, key: str) -> tuple[Path, Path]:
        sub = self.cache_dir / key[:2]
        return sub / f"{key}.body", sub / f"{key}.json"

    def request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | list[tuple[str, str]] | None = None,
        max_age_s: float = 0.0,
        use_cache: bool = True,
    ) -> FetchResult:
        """Fetch ``url`` politely, serving from / revalidating against the disk cache."""
        key = _cache_key(method, url, data)
        body_path, meta_path = self._paths(key)
        meta: dict[str, Any] = {}
        if use_cache and meta_path.exists() and body_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if time.time() - float(meta.get("fetched_at", 0)) < max_age_s:
                return FetchResult(
                    url,
                    int(meta["status"]),
                    body_path.read_bytes(),
                    str(meta.get("content_type", "")),
                    True,
                    float(meta["fetched_at"]),
                )

        headers: dict[str, str] = {}
        if meta.get("etag"):
            headers["If-None-Match"] = str(meta["etag"])
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = str(meta["last_modified"])

        self._throttle()
        log.debug("%s %s", method, url)
        content: bytes | None = None
        if data is not None:
            pairs = list(data.items()) if isinstance(data, dict) else data
            content = urllib.parse.urlencode(pairs).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self._send(method, url, content, headers)
        now = time.time()

        if resp.status_code == 304 and body_path.exists():
            meta["fetched_at"] = now
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            return FetchResult(
                url,
                int(meta["status"]),
                body_path.read_bytes(),
                str(meta.get("content_type", "")),
                True,
                now,
            )

        body = resp.content
        if is_block_page(body):
            raise SourceBlockedError(f"bot-protection page returned for {url}")
        resp.raise_for_status()

        result = FetchResult(
            str(resp.url),
            resp.status_code,
            body,
            resp.headers.get("content-type", ""),
            False,
            now,
        )
        if use_cache:
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(body)
            meta_path.write_text(
                json.dumps(
                    {
                        "url": url,
                        "status": resp.status_code,
                        "content_type": result.content_type,
                        "etag": resp.headers.get("etag"),
                        "last_modified": resp.headers.get("last-modified"),
                        "fetched_at": now,
                    }
                ),
                encoding="utf-8",
            )
        return result

    def get(self, url: str, **kw: Any) -> FetchResult:
        """GET shortcut."""
        return self.request("GET", url, **kw)

    def post(
        self, url: str, data: dict[str, Any] | list[tuple[str, str]], **kw: Any
    ) -> FetchResult:
        """POST (form-encoded) shortcut."""
        return self.request("POST", url, data=data, **kw)
