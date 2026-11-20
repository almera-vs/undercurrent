"""Score the corporate-calendar and holder extractors against hand labels.

Usage: uv run python scripts/score_calendar.py

Three sets in ``tests/fixtures/labelled_calendar/``: dividends (stage, amount per share,
record date, payment date), threshold notices (holder, stake before and after, direction)
and general-meeting lists (each holder and its share of all votes). Batch 1 was used to
write the rules. Batch 2 was labelled after them, but a few of its misses were then fixed, so
it is not a clean hold-out either: both scores are optimistic.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

from undercurrent.corp_calendar import extract_dividend
from undercurrent.ownership import extract_holding_change, extract_meeting_list, holder_key

DIR = Path(__file__).parents[1] / "tests/fixtures/labelled_calendar"
DIVIDEND_FIELDS = ("stage", "dps", "record_date", "pay_date")
NOTICE_FIELDS = ("holder", "pct_before", "pct_after", "direction")


def _rows(name: str) -> list[dict[str, Any]]:
    path = DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _eq(pred: Any, gold: Any) -> bool:
    if isinstance(pred, date):
        pred = pred.isoformat()
    if pred is None or gold is None:
        return pred is None and gold is None
    if isinstance(gold, int | float) and isinstance(pred, int | float):
        return math.isclose(float(pred), float(gold), abs_tol=1e-6)
    return str(pred) == str(gold)


def _tally(per_field: dict[str, dict[str, int]], field: str, ok: bool, batch: int) -> None:
    f = per_field.setdefault(field, {"n": 0, "ok": 0, "b2_n": 0, "b2_ok": 0})
    f["n"] += 1
    f["ok"] += ok
    if batch == 2:
        f["b2_n"] += 1
        f["b2_ok"] += ok


def score_dividends() -> dict[str, Any]:
    """Field-level accuracy of :func:`extract_dividend`."""
    per: dict[str, dict[str, int]] = {}
    misses = []
    rows = _rows("dividends.jsonl")
    for r in rows:
        d = extract_dividend(r["title"], r["body_text"], date.fromisoformat(r["published"]))
        for f in DIVIDEND_FIELDS:
            pred = getattr(d, f) if d is not None else None
            ok = _eq(pred, r["label"][f])
            _tally(per, f, ok, r["batch"])
            if not ok:
                misses.append((r["report_id"], f, pred, r["label"][f]))
    return _summary(rows, per, misses)


def score_notices() -> dict[str, Any]:
    """Field-level accuracy of :func:`extract_holding_change`; holders compare by key."""
    per: dict[str, dict[str, int]] = {}
    misses = []
    rows = _rows("holder_notices.jsonl")
    for r in rows:
        h = extract_holding_change(r["title"], r["body_text"])
        for f in NOTICE_FIELDS:
            pred, gold = getattr(h, f), r["label"][f]
            ok = holder_key(pred) == holder_key(gold) if f == "holder" else _eq(pred, gold)
            _tally(per, f, ok, r["batch"])
            if not ok:
                misses.append((r["report_id"], f, pred, gold))
    return _summary(rows, per, misses)


def score_lists() -> dict[str, Any]:
    """Precision and recall of meeting-list rows: the holder's key and its share of votes."""
    found = gold_n = hit = 0
    misses = []
    rows = _rows("meeting_lists.jsonl")
    for r in rows:
        pred = {
            (holder_key(x.holder), round(x.pct_total, 4))
            for x in extract_meeting_list(r["body_text"])
        }
        gold = {(holder_key(x["holder"]), round(x["pct_total"], 4)) for x in r["label"]}
        found += len(pred)
        gold_n += len(gold)
        hit += len(pred & gold)
        misses += [(r["report_id"], "missed", g) for g in gold - pred]
        misses += [(r["report_id"], "extra", p) for p in pred - gold]
    return {
        "reports": len(rows),
        "precision": hit / found if found else math.nan,
        "recall": hit / gold_n if gold_n else math.nan,
        "misses": misses,
    }


def _summary(
    rows: list[dict[str, Any]], per: dict[str, dict[str, int]], misses: list[Any]
) -> dict[str, Any]:
    n = sum(f["n"] for f in per.values())
    ok = sum(f["ok"] for f in per.values())
    hn = sum(f["b2_n"] for f in per.values())
    hok = sum(f["b2_ok"] for f in per.values())
    return {
        "reports": len(rows),
        "field_accuracy": ok / n if n else math.nan,
        "batch2_accuracy": hok / hn if hn else math.nan,
        "per_field": per,
        "misses": misses,
    }


def main() -> int:
    """CLI entry."""
    for name, s in (("dividends", score_dividends()), ("threshold notices", score_notices())):
        print(f"{name}: {s['reports']} reports")
        print(f"  field accuracy {s['field_accuracy']:.1%}, batch 2 {s['batch2_accuracy']:.1%}")
        for f, v in s["per_field"].items():
            print(f"  {f:<12} {v['ok']}/{v['n']}  (batch 2: {v['b2_ok']}/{v['b2_n']})")
        for m in s["misses"]:
            print(f"  MISS {m[0]} {m[1]}: pred={m[2]!r} gold={m[3]!r}")
    s = score_lists()
    print(f"meeting lists: {s['reports']} reports")
    print(f"  precision {s['precision']:.1%}, recall {s['recall']:.1%}")
    for m in s["misses"]:
        print(f"  {m[1].upper()} {m[0]}: {m[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
