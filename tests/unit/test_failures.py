"""Why a run failed, in words: the reason the alert carries."""

from datetime import UTC, date, datetime

import httpx
import polars as pl
import pytest

from undercurrent import failures, lake, pipeline
from undercurrent.cli import failure_details
from undercurrent.fetch import SourceBlockedError

LISTING = httpx.Request("POST", "https://www.gpw.pl/ajaxindex.php")


def _raised(exc: BaseException) -> BaseException:
    """``exc`` as the run sees it: raised, with a traceback, inside a wrapping error."""
    try:
        try:
            raise exc
        except BaseException as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        return outer


def test_a_timeout_names_the_site_and_the_request():
    why = failures.explain(_raised(httpx.ReadTimeout("timed out", request=LISTING)))
    assert why.startswith("gpw.pl (the exchange's site) (POST www.gpw.pl/ajaxindex.php)")
    assert "did not answer in time (ReadTimeout" in why
    assert f"tried {failures.ATTEMPTS} times" in why
    assert "an outage on its side, not a fault in Undercurrent" in why
    assert failures.is_outage(why)


def test_no_connection_and_a_dropped_connection():
    why = failures.explain(httpx.ConnectError("Name or service not known", request=LISTING))
    assert "no connection to gpw.pl" in why and failures.is_outage(why)
    why = failures.explain(httpx.RemoteProtocolError("peer closed", request=LISTING))
    assert "dropped the connection (RemoteProtocolError" in why and failures.is_outage(why)


def test_bot_protection_needs_a_look_not_a_retry():
    why = failures.explain(SourceBlockedError("bot-protection page returned for https://x"))
    assert "bot-protection page" in why and not failures.is_outage(why)


def test_our_own_error_points_at_the_line():
    def boom() -> None:
        {}["missing"]

    try:
        boom()
    except KeyError as exc:
        why = failures.explain(exc)
    assert why.startswith("an error in Undercurrent's own code")
    assert "KeyError" in why and not failures.is_outage(why)


class _Answer:
    def __init__(self, status: int) -> None:
        self.status_code = status


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (_Answer(200), "answers again now: the outage was short"),
        (_Answer(503), "still answers with errors now (HTTP 503)"),
        (httpx.ConnectTimeout("t"), "still does not answer now (ConnectTimeout)"),
    ],
)
def test_the_alert_checks_whether_the_site_is_back(monkeypatch, answer, expected):
    def fake_get(url: str, **kw: object) -> _Answer:
        assert url == "https://www.gpw.pl/"
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(failures.httpx, "get", fake_get)
    why = failures.explain(httpx.ReadTimeout("t", request=LISTING))
    assert expected in str(failures.reachable_now(why))
    assert failures.reachable_now("an error in Undercurrent's own code: KeyError") is None


DAY = date(2026, 11, 19)


def _run(root, status: str, failure: str | None) -> None:
    res = pipeline.RunResult(
        run_date=DAY, started_at=datetime(2026, 11, 19, 17, 36, tzinfo=UTC), status=status
    )
    res.failure = failure
    pipeline._record_run(root, res)


def test_alert_for_a_failed_run_says_why_and_what_next(tmp_path, monkeypatch):
    monkeypatch.setattr(failures.httpx, "get", lambda url, **kw: _Answer(200))
    why = failures.explain(httpx.ReadTimeout("t", request=LISTING))
    _run(tmp_path, "failed", why)
    text = failure_details(tmp_path, DAY, "undercurrent run")
    assert f"Reason: {why}" in text
    assert "answers again now" in text
    assert "The next attempt, within 30 minutes, retries the day" in text


def test_alert_when_a_later_step_failed(tmp_path):
    _run(tmp_path, "ok", None)
    text = failure_details(tmp_path, DAY, "commit digest archive")
    assert 'the data run itself finished (ok); a later step "commit digest archive" failed' in text


def test_alert_when_nothing_was_recorded(tmp_path):
    text = failure_details(tmp_path, DAY, "setup")
    assert 'the job stopped at "setup" before the run recorded anything' in text


def test_the_run_records_its_reason(tmp_path):
    why = failures.explain(httpx.ReadTimeout("t", request=LISTING))
    _run(tmp_path, "failed", why)
    runs = lake.read_state(tmp_path, "runs")
    assert runs is not None
    assert runs.filter(pl.col("run_date") == DAY)["failure"].to_list() == [why]


def test_the_client_names_the_request_it_gave_up_on(tmp_path):
    from undercurrent.fetch import PoliteClient

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out")  # no request attached

    client = PoliteClient(
        tmp_path,
        min_interval_s=0,
        max_jitter_s=0,
        retry_pauses_s=(0.0,),
        sleep=lambda s: None,
        transport=httpx.MockTransport(handler),
    )
    with client, pytest.raises(httpx.ReadTimeout) as info:
        client.post("https://www.gpw.pl/ajaxindex.php", data={"a": "b"}, use_cache=False)
    why = failures.explain(_raised(info.value))
    assert why.startswith("gpw.pl (the exchange's site) (POST www.gpw.pl/ajaxindex.php)")
