# Labelled insider notifications

The plan called for 300+ hand-labelled insider notification **PDFs**. The PDF attachments are
behind a bot-protection wall (see `docs/DECISIONS.md` → "Blocker: PDF attachments"), so these
fixtures are labelled **report-page texts** (`title` + `Treść raportu`) of real Art. 19 MAR
reports. The directory keeps its original name.

`insider_labels.jsonl` has one JSON object per report: `report_id`, `source_url`, `title`,
`body_text`, `labels`, `labelled_by`, `labelled_on`.

## Labelling guide

Label only what the text states. Never infer. `null` means "not stated in this text".

| field | rule |
|---|---|
| `person` | Whoever made the notification / trade, as written (inflected form), e.g. `Adama Nowickiego`, `PKO Bank Polski S.A.`. Not the PDMR a closely associated entity is linked to. |
| `role` | `CEO`, `CFO`, `board`, `supervisory_board`, `closely_associated`. A closely associated person or entity is always `closely_associated`. |
| `side` | `buy` (incl. subscription for new shares), `sell`, `mixed`, `pledge`. |
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
