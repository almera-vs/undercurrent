"""The blind labelling sheet and the guard that keeps labels from being overwritten."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from undercurrent import news
from undercurrent import news_coverage as nc
from undercurrent import news_sheet as ns
from undercurrent.news import NewsItem
from undercurrent.news_common import CompanyInfo, NameIndex

ALFA = "PLTEST000001"
TUE = date(2026, 11, 17)
NOW = datetime(2026, 11, 17, 19, 30, tzinfo=UTC)


def _warsaw(m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(2026, m, d, hh, mm, tzinfo=news.WARSAW).astimezone(UTC)


# ---- the guard --------------------------------------------------------------------------


def test_a_labelled_sheet_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    pl.DataFrame({"title": ["a"], "correct": ["y"]}).write_csv(path)
    new = pl.DataFrame({"title": ["b"], "correct": [None]}, schema_overrides={"correct": pl.String})
    written = nc.write_sheet(new, path, "correct", now=lambda: datetime(2026, 11, 24, 21, 5, 0))
    assert written.name == "sample-20261124-210500.csv"
    assert pl.read_csv(path)["correct"].to_list() == ["y"]  # the labels survive
    assert pl.read_csv(written)["title"].to_list() == ["b"]


def test_an_unlabelled_sheet_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "sample.csv"
    pl.DataFrame({"title": ["a"], "correct": [" "]}).write_csv(path)
    new = pl.DataFrame({"title": ["b"], "correct": [None]}, schema_overrides={"correct": pl.String})
    assert nc.write_sheet(new, path, "correct") == path
    assert pl.read_csv(path)["title"].to_list() == ["b"]


# ---- the sheet --------------------------------------------------------------------------


def _store(tmp_path: Path) -> nc.Store:
    store = nc.Store(tmp_path)
    titles = [
        ("Alfa Testowa podpisała umowę za 120 mln zł", _warsaw(11, 17, 10, 5)),
        ("Alfa Testowa: wywiad z prezesem", _warsaw(11, 17, 8, 0)),
        ("Alfa Testowa obniża prognozy", _warsaw(11, 17, 18, 30)),  # after the close
        ("Alfa Testowa miała zysk w III kw.", _warsaw(11, 16, 12, 0)),  # previous session
    ]
    store.add_items(
        news.BANKIER,
        ALFA,
        [
            NewsItem(ALFA, news.BANKIER, "Bankier", at, "minute", t, f"https://x.test/{k}", NOW)
            for k, (t, at) in enumerate(titles)
        ],
    )
    return store


def _reports() -> pl.DataFrame:
    base: dict[str, Any] = {
        "report_id": "r1",
        "isin": ALFA,
        "system": "ESPI",
        "kind": "Bieżący",
        "signal_type": "inside_info",
        "subtype": None,
        "title": "Zawarcie znaczącej umowy",
        "subject": None,
        "attachments_json": None,
        "source_url": "https://gpw.test/1",
        "known_at": _warsaw(11, 17, 9, 40),
    }
    routine = {
        **base,
        "report_id": "r2",
        "signal_type": "other",
        "title": "Informacja o stanie stosowania Dobrych Praktyk",
        "known_at": _warsaw(11, 17, 9, 0),
    }
    return pl.DataFrame([base, routine])


def _moves() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "isin": [ALFA, ALFA, ALFA],
            "trade_date": [TUE, date(2026, 11, 18), date(2026, 11, 19)],
            "liquidity_tier": ["liquid"] * 3,
            "surprise": [2.2, -2.4, 3.1],
            "resid": [0.031, -0.04, 0.05],
            "cause": ["news", "pending", "none"],
        }
    )


def test_the_band_takes_complete_liquid_moves_only() -> None:
    picked = ns.candidates(_moves(), tier="liquid", min_z=2.0, max_z=2.5)
    assert picked["trade_date"].to_list() == [TUE]  # 18 Nov is pending, 19 Nov outside the band


def test_the_sheet_lists_the_sessions_items_oldest_first_under_row_zero(tmp_path: Path) -> None:
    info = CompanyInfo(ALFA, "ALFA", "Alfa Testowa")
    moves = ns.candidates(_moves(), tier="liquid", min_z=2.0, max_z=2.5)
    sheet, pop = ns.build(
        moves,
        _store(tmp_path),
        {ALFA: info},
        _reports(),
        NameIndex({ALFA: info.company()}),
        per_sign=5,
        seed=1,
    )
    assert (pop.moves, pop.with_items, pop.sampled_up, pop.sampled_down) == (1, 1, 1, 0)
    assert sheet["title"].to_list() == [
        ns.NOTHING,
        "Alfa Testowa: wywiad z prezesem",
        "Zawarcie znaczącej umowy",
        "Alfa Testowa podpisała umowę za 120 mln zł",
    ]
    assert sheet["item_no"].to_list() == [0, 1, 2, 3]
    assert sheet["when"].to_list() == ["", "17.11 08:00", "17.11 09:40", "17.11 10:05"]
    row = sheet.row(0, named=True)
    assert (row["move"], row["direction"], row["move_id"]) == ("+3.1%", "up", f"{ALFA}:2026-11-17")
    assert "cause" not in sheet.columns  # blind


def test_check_counts_labels_and_flags_two_marks(tmp_path: Path) -> None:
    rows = [
        {"move_id": "a", "item_no": 0, ns.LABEL: None},
        {"move_id": "a", "item_no": 1, ns.LABEL: "y"},
        {"move_id": "b", "item_no": 0, ns.LABEL: "Y "},
        {"move_id": "b", "item_no": 1, ns.LABEL: None},
        {"move_id": "c", "item_no": 0, ns.LABEL: "y"},
        {"move_id": "c", "item_no": 1, ns.LABEL: "y"},
        {"move_id": "d", "item_no": 0, ns.LABEL: None},
    ]
    path = tmp_path / "sheet.csv"
    pl.DataFrame(rows).write_csv(path)
    p = ns.check(pl.read_csv(path, infer_schema_length=0))  # as the CLI reads it: all text
    assert (p.moves, p.labelled, p.nothing, p.errors) == (4, 2, 1, ["c"])
