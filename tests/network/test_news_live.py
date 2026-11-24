"""The news sources as they are today: each still parses for a liquid stock.

Run by the probe workflow with ``UNDERCURRENT_NETWORK=1``; skipped otherwise. A failure here
means a portal changed its markup or started refusing us, which the nightly run would only
show as fewer headlines.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from undercurrent import news
from undercurrent.fetch import PoliteClient
from undercurrent.news_common import CompanyInfo

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("UNDERCURRENT_NETWORK") != "1", reason="needs UNDERCURRENT_NETWORK=1"
    ),
]

XTB = CompanyInfo("PLXTRDM00011", "XTB", "XTB").company()
SINCE = datetime.now(UTC) - timedelta(days=60)


@pytest.fixture
def client() -> Iterator[PoliteClient]:
    c = PoliteClient(timeout_s=20.0)
    yield c
    c.close()


@pytest.mark.parametrize("source", [news.BIZNESRADAR, news.BANKIER])
def test_portal_page_is_the_company_and_has_dated_items(client: PoliteClient, source: str) -> None:
    page = news.fetch_company(
        client, source, XTB, since=SINCE, until=datetime.now(UTC), max_pages=1
    )
    assert page.outcome is news.Outcome.OK, page.detail
    assert all(i.title and i.url and i.published_at >= SINCE for i in page.items)


def test_biznesradar_symbol_resolves_through_its_redirect(client: PoliteClient) -> None:
    info = CompanyInfo("PLZATRM00012", "GRUPAAZOTY", "Grupa Azoty")
    outcome, sym, detail = news.resolve_symbol(
        client, news.BIZNESRADAR, info.company(), "GRUPAAZOTY"
    )
    assert outcome is news.Outcome.OK and sym is not None, detail
    assert sym.symbol == "ATT" and sym.page_name == "GRUPA AZOTY"


def test_google_news_feed_parses(client: PoliteClient) -> None:
    page = news.fetch_google(client, XTB, since=SINCE, until=datetime.now(UTC))
    assert page.outcome is news.Outcome.OK, page.detail
    assert any(i.outlet for i in page.items)
