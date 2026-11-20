# Labelled calendar and holder reports

Hand-labelled **report texts** (`title` + `Treść raportu`) of real current reports, used to
measure the corporate-calendar extractor (`corp_calendar.py`) and the holder extractor
(`ownership.py`). Reports were drawn at random from the lake (2015–2018, 2025–2026). Names
of natural persons are replaced with fictional ones in the same grammatical case; company
names are real. Bodies longer than 9,000 characters (general-meeting documents) keep the
stretch from shortly before the first mention of the dividend.

| file | one object per report | label |
|---|---|---|
| `dividends.jsonl` | dividend notices, recommendations, meeting resolutions | `stage`, `dps`, `record_date`, `pay_date` |
| `holder_notices.jsonl` | Art. 69/70 pkt 1 threshold notices | `holder`, `pct_before`, `pct_after`, `direction` |
| `meeting_lists.jsonl` | Art. 70 pkt 3 lists of holders at a general meeting | each holder and its `pct_total` |

`batch` 1 was used to write the rules. `batch` 2 was labelled after them, but some of its
misses were then fixed, so it is not a clean hold-out: both scores are optimistic.

## Labelling guide

Label only what the text states. Never infer. `null` means "not stated in this text".

| field | rule |
|---|---|
| `stage` | `resolved` (the general meeting's resolution, or the exchange's dividend form), `proposed` (board or supervisory-board recommendation, a draft resolution), `interim` (an advance on the year's dividend), `none` (no dividend). |
| `dps` | Gross amount per share, in the currency stated. With two instalments, the total. With share classes, the ordinary share. |
| `record_date` | *Dzień dywidendy*: the day the list of entitled holders is fixed. ISO. |
| `pay_date` | The payment date; with instalments, the first. ISO. |
| `holder` | The holder as written (inflected form). With several holders or an agreement, the first named. Compared by `ownership.holder_key`, so legal forms and declension do not matter. |
| `pct_before`, `pct_after` | The holder's share of **all votes** before and after, as stated. Not the share of capital, not the size of the transaction. |
| `direction` | `up` or `down`, from the stakes or the wording; `null` when neither says. |
| `pct_total` | A list row's share of all votes in the company (not of the votes at the meeting). |

## Scoring

```bash
uv run python scripts/score_calendar.py
```

`tests/unit/test_calendar_accuracy.py` fails when field accuracy drops below its gate.
