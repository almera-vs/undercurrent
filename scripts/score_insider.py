"""Score insider-field extraction against hand labels.

Usage: uv run python scripts/score_insider.py [labels.jsonl]
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

from undercurrent.parse import extract_insider_fields

FIELDS = ("person", "role", "side", "volume", "avg_price", "value", "trade_date", "venue")
DEFAULT = Path(__file__).parents[1] / "tests/fixtures/labelled_pdfs/insider_labels.jsonl"


def _eq(pred: Any, gold: Any) -> bool:
    if isinstance(pred, date):
        pred = pred.isoformat()
    if pred is None or gold is None:
        return pred is None and gold is None
    if isinstance(gold, (int, float)) and isinstance(pred, (int, float)):
        return math.isclose(float(pred), float(gold), rel_tol=1e-9, abs_tol=1e-9)
    return str(pred) == str(gold)


def score(path: Path = DEFAULT) -> dict[str, Any]:
    """Return accuracy numbers and the list of mismatches."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    total = correct = stated = stated_ok = 0
    per_field = {f: {"n": 0, "ok": 0, "stated": 0} for f in FIELDS}
    misses = []
    for row in rows:
        pred = extract_insider_fields(row["title"], row["body_text"])
        for f in FIELDS:
            p, g = getattr(pred, f), row["labels"][f]
            ok = _eq(p, g)
            total += 1
            correct += ok
            per_field[f]["n"] += 1
            per_field[f]["ok"] += ok
            if g is not None:
                stated += 1
                stated_ok += ok
                per_field[f]["stated"] += 1
            if not ok:
                misses.append(
                    {"report_id": row["report_id"], "field": f, "pred": str(p), "gold": g}
                )
    return {
        "reports": len(rows),
        "field_accuracy": correct / total if total else math.nan,
        "stated_field_accuracy": stated_ok / stated if stated else math.nan,
        "per_field": per_field,
        "misses": misses,
    }


def main() -> int:
    """CLI entry."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    s = score(path)
    print(f"reports: {s['reports']}")
    print(f"field-level accuracy (all fields): {s['field_accuracy']:.1%}")
    print(f"accuracy on stated fields:         {s['stated_field_accuracy']:.1%}")
    for f, v in s["per_field"].items():
        print(f"  {f:<11} acc {v['ok']}/{v['n']}  stated in {v['stated']}/{v['n']}")
    for m in s["misses"]:
        print(f"  MISS {m['report_id']} {m['field']}: pred={m['pred']!r} gold={m['gold']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
