# Labelled insider notifications

Hand-labelled **report texts** (`title` + `Treść raportu`) of real MAR Art. 19 reports, used
to measure the insider-field extractor. The notification PDFs themselves are behind bot
protection (see `docs/DECISIONS.md` → "Classification and insider fields"), so labels cover
what the report text states. Names of natural persons in these texts and in the page fixtures
are replaced with fictional ones; company names are real.

`insider_labels.jsonl` has one JSON object per report: `report_id`, `source_url`, `title`,
`body_text`, `labels`, `labelled_by`, `labelled_on`.

## Labelling guide

Label only what the text states. Never infer. `null` means "not stated in this text".

| field | rule |
|---|---|
| `person` | Whoever made the notification / trade, as written (inflected form), e.g. `Adama Nowickiego`, `PKO Bank Polski S.A.`. Not the PDMR a closely associated entity is linked to. |
| `role` | `CEO`, `CFO`, `board`, `supervisory_board`, `closely_associated`, `pdmr` (only "a person discharging managerial responsibilities", no function given). A closely associated person or entity is always `closely_associated`. |
| `side` | `buy` (incl. subscription for new shares), `sell`, `mixed`, `pledge`, `gift`, `incentive` (incentive-plan grant). |
| `volume` | Number of shares in the transaction (or pledged). |
| `avg_price` | Price per share in PLN, as stated. |
| `value` | Total value in PLN **only if stated**. Not computed. |
| `trade_date` | Date of the transaction (not the date the issuer received the notice). ISO. |
| `venue` | `GPW`, `NewConnect`, `off_market`, `block_trade`. |

## Scoring

```bash
uv run python scripts/score_insider.py
```

It prints field-level accuracy over all fields (a correctly-null field counts as correct),
accuracy on stated fields only, and per-field coverage. CI fails below the threshold set in
`tests/unit/test_insider_accuracy.py`.
