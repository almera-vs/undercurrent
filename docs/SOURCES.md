# Sources

Every endpoint Undercurrent touches. Last verified on **2026-11-09** from a residential
connection and from a GitHub-hosted `ubuntu-latest` runner, using the
[`probe-sources`](../.github/workflows/probe.yml) workflow (`gh workflow run probe-sources`).

**robots.txt.** `https://www.gpw.pl/robots.txt` and `https://newconnect.pl/robots.txt` both
contain only `User-agent: *` / `Disallow: /pub/NEWCONNECT/DI/EUROSNACK`. Nothing we use is
disallowed.

**Client behaviour** (all sources): one request per second plus 0–0.5 s jitter,
User-Agent `undercurrent/0.1 (+https://github.com/almera-vs/undercurrent)`, conditional
requests where the server sends validators, on-disk cache in `.cache/http`.

## Summary

| # | Source | Used for | Residential | GitHub runner | Status |
|---|---|---|---|---|---|
| 1 | gpw.pl ESPI/EBI listing (AJAX) | main-market report index | 200 | 200 | ✅ used |
| 2 | newconnect.pl ESPI/EBI listing (AJAX) | NewConnect report index | 200 | 200 ¹ | ✅ used |
| 3 | `komunikat?geru_id=` report page | legal basis, body, attachment list | 200 | 200 | ✅ used |
| 4 | `attachment/…` PDF download | insider notification forms | **WAF reject** | **WAF reject** | ⛔ blocked |
| 5 | gpw.pl quote archive, shares (XLS) | main-market daily OHLCV | 200 | 200 | ✅ used |
| 6 | gpw.pl quote archive, indices (HTML) | WIG benchmark | 200 | 200 | ✅ used |
| 7 | newconnect.pl daily statistics (XLSX/XLS) | NewConnect daily OHLCV | 200 | 200 | ✅ used |
| 8 | espiebi.pap.pl | alternative ESPI/EBI mirror | **Incapsula challenge** | 200 | ⛔ not used |
| 10 | xtb.com stock pages | optional link to the company on XTB | 200 | — | ✅ used, links only |
| 11 | filings.xbrl.org (ESEF, xBRL-JSON) | company fundamentals, fiscal 2020–2024 | 200 | — | ✅ used, manual |
| 12 | api.gleif.org | LEI → ISIN for those filings | 200 | — | ✅ used, manual |
| 13 | gpw.pl/wskazniki | shares, book value, earnings, sector | 200 | — | ✅ used |
| 14 | logos.xtb.com, exchange company cards, company websites | stock icons | 200 | — | ✅ used, weekly |
| 15 | bankier.pl copies of ESEF files | capex, depreciation (supplement to 16) | 200 | — | ✅ used, weekly |
| 16 | gpw.pl report pages, selected financial data | annual, half-year, quarterly figures | 200 | — | ✅ used, weekly |

¹ The NewConnect listing uses the same `ajaxindex.php` handler on the same infrastructure as
row 1; confirmed from the runner in the same probe run, along with a NewConnect report page.

---

## 1. ESPI/EBI report listing, main market (gpw.pl)

- **URL:** `POST https://www.gpw.pl/ajaxindex.php` (form-encoded)
- **Form** (what the `/komunikaty` page's `#espi-union-reports` form serialises):

  ```
  action=GPWEspiReportUnion  start=ajaxSearch  page=komunikaty  format=html  lang=PL
  letter=  offset=0  limit=100
  categoryRaports[]=EBI  categoryRaports[]=ESPI
  typeRaports[]=RB  typeRaports[]=P  typeRaports[]=Q  typeRaports[]=O  typeRaports[]=R
  searchText=  date=DD-MM-YYYY
  ```

  `RB` current, `Q` quarterly, `P` semi-annual, `R` annual, `O` other.
- **Format:** HTML fragment, one `<li>` per report, newest first.
- **Coverage:** main-market (GPW) issuers only. A whole day fits in one page: on 2026-11-06 the
  day returned 41 reports with `limit=100`, and `offset=41` returned nothing. We still page
  until an empty response, in case a busy day exceeds the limit.
- **History:** the `date` filter works back to at least 2015-01-05 (checked: reports
  `1/2015`, `2/2015` from 05-01-2015 23:17–23:41).
- **Update timing:** near real time; ESPI publishes around the clock (the 2026-11-06 day ran
  from 00:00:11 to 20:55:52).
- **Validators:** none (no ETag / Last-Modified). Cached by URL+form for re-runs.
- **Sample** (2026-11-06, trimmed):

  ```html
  <li style="padding: 15px 0;">
    <span class="date">06-11-2026 20:14:25 | Bieżący | ESPI | 38/2026</span>
    <strong class="name">
      <a href="komunikat?geru_id=500186&amp;title=Ca%C5%82kowita+sp%C5%82ata+...">
        CI GAMES SPÓŁKA EUROPEJSKA (PLCTINT00018)
      </a>
    </strong>
    <span class="profit ..."><small>Zmiana</small> 2,29% ...</span>
    <span class="summary ..."><small>Kurs</small>2,46</span>
    <p>Całkowita spłata kredytów w PKO Bank Polski S.A. oraz Erste Bank Polska S.A.</p>
  ```

  Fields we take: `geru_id` (stable numeric ID → `report_id`), timestamp (Europe/Warsaw
  local), report kind, system (ESPI/EBI), report number, issuer name, **ISIN** (in
  parentheses), title. The `Zmiana`/`Kurs` values are a live quote at render time, not
  point-in-time; we ignore them.

## 2. ESPI/EBI report listing, NewConnect (newconnect.pl)

- **URL:** `POST https://newconnect.pl/ajaxindex.php`
- **Form:** identical to §1 except `page=spolki-komunikaty-spolek`.
- **Coverage:** NewConnect issuers, both EBI reports and ESPI reports (MAR notifications from
  NC issuers go through ESPI). 2026-11-06: 47 reports (28 EBI, 19 ESPI).
- **Sample:** same `<li>` markup as §1, for example
  `06-11-2026 23:53:38 | Kwartalny | EBI | 9/2026`.

## 3. Report page

- **URL:** `GET https://www.gpw.pl/komunikat?geru_id={id}` (main market) and
  `GET https://newconnect.pl/komunikat?geru_id={id}` (NewConnect). `geru_id` ranges are
  separate per site (gpw.pl ≈ 500 000 in Nov 2026; newconnect.pl ≈ 244 800).
- **Format:** HTML page that embeds the original ESPI document. The ESPI template has labelled
  table rows:
  - `Temat` (subject)
  - `Podstawa prawna` (legal basis), for example
    `Art. 19 ust. 3 Rozporządzenia MAR - informacja o transakcjach wykonywanych przez osoby
    pełniące obowiązki zarządcze`, or
    `Art. 70 pkt 1 Ustawy o ofercie - nabycie lub zbycie znacznego pakietu akcji`
  - `Treść raportu:` (body)
  - `Spis zalacznikow` / `Załączniki` (attachment list, see §4)
  - issuer block: NIP, REGON, `Data sporzadzenia`, `Numer`, www
- EBI pages (NewConnect) use a different, lighter template: `Treść raportu` exists, and the
  legal basis appears in the body (usually `§ … Załącznika Nr 3 do Regulaminu ASO`). The
  parser handles both templates.
- **Validators:** none. Report pages are immutable once published (corrections come as new
  reports), so a cached page is never re-fetched.
- **Sample** (geru_id 500131, GRODNO S.A., 2026-11-06 08:30:50):

  > Podstawa prawna: Art. 19 ust. 3 Rozporządzenia MAR - informacja o transakcjach
  > wykonywanych przez osoby pełniące obowiązki zarządcze
  >
  > Treść raportu: Zarząd Grodno S.A. _Spółka_ informuje o otrzymaniu w dniu 5 listopada
  > 2026 r. powiadomienia w trybie art. 19 ust. 1 MAR od Pana Adama Nowickiego - Wiceprezesa
  > Zarządu Spółki. W załączeniu Spółka przekazuje treść otrzymanego powiadomienia.

## 4. Report attachments (PDF) — BLOCKED

- **URL:** relative links on the report page, for example
  `attachment/zal01_Powiadomienie_art_19_MAR_MG.pdf,20261106_083050_0000622750,20261106`,
  resolved against `<base href="/">` → `https://www.gpw.pl/attachment/…`.
- **Result:** HTTP 200 with an F5 BIG-IP ASM interstitial (`Request Rejected … Your support
  ID is: …`) plus an obfuscated JavaScript challenge, never the PDF. Reproduced on four
  different attachments, from the residential IP and from the GitHub runner, with and without
  the session cookie and Referer of the report page.
- **newconnect.pl:** ESPI attachments there use the same `attachment/…` path and are blocked
  the same way (checked 2026-11-09 on an Art. 19 notification). NewConnect **EBI** reports in
  the 2026 format link their files under `/ebi/files/gpwdata/…` instead, and those download
  normally (HTTP 200, `application/pdf`). Their `main.xhtml` holds only the header and
  signatures; the report text itself is in the PDFs, which we don't parse (see METHODOLOGY).
- **Not used:** this is bot protection aimed at automated clients, so these URLs are not
  fetched (no headless browser, no challenge solving, no browser User-Agent). See
  DECISIONS.md → "Fetching".

## 5. Daily prices, main market (gpw.pl quote archive)

- **URL (XLS):**
  `GET https://www.gpw.pl/archiwum-notowan?fetch=1&type=10&instrument=&date=DD-MM-YYYY`
  - `type=10` shares, `type=1` indices (other types: 17 pre-emptive rights, 37 rights to
    shares, 241 ETF, …)
- **Format:** legacy Excel `.xls` (CFB), one sheet `Worksheet`,
  `Content-Disposition: attachment;filename="_2026-11-06_akcje.xls"`. Columns:
  `Data, Nazwa, ISIN, Waluta, Kurs otwarcia, Kurs max, Kurs min, Kurs zamknięcia, Zmiana,
  Wolumen, Liczba Transakcji, Obrót, Liczba otwartych pozycji, Wartość otwartych pozycji,
  Cena nominalna`. `Obrót` is in thousands of PLN.
- **Sample row:** `2026-11-06, 06MAGNA, PLNFI0600010, PLN, 2.17, 2.23, 2.16, 2.23, -0.89,
  2368, 12, 5.13, 0, 0, 0`
- **History:** same columns for 2015-01-05 (476 rows).
- **Non-trading days:** an empty table.
- **Prices are unadjusted** (raw session prices). No reference price column. Adjustment is
  our job (see METHODOLOGY.md).
- **Validators:** `Cache-Control: max-age=0`, no ETag. Past days never change, so a stored
  past day is never re-fetched.
- **Timing:** the continuous session ends at 17:00 Warsaw time and the closing auction at
  ≈17:05. The archive has the day by the time the nightly job runs at 17:47 UTC
  (18:47 CET / 19:47 CEST).

## 6. WIG benchmark (gpw.pl quote archive, indices)

- **URL (HTML):** `GET https://www.gpw.pl/archiwum-notowan?fetch=0&type=1&instrument=&date=DD-MM-YYYY&show_x=Poka%C5%BC+wyniki`
  (the XLS variant, `fetch=1&type=1`, also works and is what ingest uses).
- **Format:** HTML table with `Nazwa, Waluta, Kurs otwarcia, Kurs maksymalny, Kurs minimalny,
  Kurs zamknięcia, Zmiana kursu %, Wartość obrotu (w tys.)`; Polish number format with
  non-breaking-space thousands separators and a decimal comma.
- **Sample row:** `WIG | | 156 000,97 | 157 349,27 | 155 224,05 | 156 043,30 | -1,25 | 2 853 068,87`
- We keep WIG (benchmark for the market model) and store all indices in bronze.

## 7. Daily prices, NewConnect (daily statistics file)

- **URL:** `GET https://newconnect.pl/pub/NEWCONNECT/statystyki/statystyki_dzienne/NC_YYYYMMDD.xlsx`
  - Older years use `.xls` (seen for 2015-01 and 2020-06). The month listing
    `https://newconnect.pl/statystyki-okresowe?page=statystyki-okresowe&year=YYYY&month=MM&day=0`
    links each file with its real extension; ingest reads it instead of guessing.
  - `https://newconnect.pl/archiwum-notowan` redirects (301) here. Calling it with quote
    archive query parameters returns a WAF reject, so we never use that URL.
- **Format:** workbook with sheets `indeksy, statystyka, akcje, prawa poboru, transakcje
  pozasesyjne, wskaźniki spółek, wskaźniki sektorowe, legenda`. Sheet `akcje` has 5 header
  rows, then: corporate-action flags (cols A–E), `Sektor, System notowań, Segment, Kod ISIN,
  Nazwa, Skrót (ticker), Waluta, Kurs zamknięcia, Kurs odniesienia, Zmiana %, Kurs otwarcia,
  min, maks, ostatni, Kurs średni zagregowany, Kurs średni (arkusz zleceń), Kurs średni
  (cross), Liczba transakcji, Wolumen, Wartość obrotu`.
  The **reference price** (`Kurs odniesienia`) is a strong corporate-action signal: when it
  differs from the previous close, the exchange adjusted for a split, dividend or rights
  issue.
- **Sample row:** `160, C, NB, PLONESL00011, 1SOLUTION, ONE, PLN, 0.0974, 0.0976, -0.2, 0.097,
  0.097, 0.0974, 0.0974, 0.0974, 0.0974, ---, 6, 2609, 254`
- **Validators:** `ETag` and `Last-Modified` (for example `"83014a-18e7b-65f9f8f20e380"`,
  `Fri, 06 Nov 2026 18:12:46 GMT`). Conditional GET is used.
- **Timing — important:** the file appears **between 16:47 and 18:50 UTC** (sampled
  2026-10-23 … 2026-11-06), which is often *after* the 17:47 UTC cron. See DECISIONS.md → "Ingest".

## 8. espiebi.pap.pl — not used

PAP's public ESPI/EBI site. From the residential IP every URL, including `robots.txt`, returns
an Imperva/Incapsula JavaScript challenge (`/_Incapsula_Resource`). From the GitHub runner the
home page sometimes returns 200. A bot-protected site that sometimes lets a datacenter IP
through is not a dependable or clearly permitted source, so we do not use it. Its data is a
subset of §1–3 anyway, apart from attachments.

## 9. Copies of ESPI attachments on financial portals (used for insider forms)

The insider notification forms (§4) are blocked at the exchange, but several financial
portals republish ESPI reports and host their own copies of the attachments. Checked on
2026-11-09 with the project's client (one request per second, project User-Agent):

| Portal | Report pages | Attachment copies | Discovery | robots.txt | Result |
|---|---|---|---|---|---|
| Parkiet (parkiet.com) | `/komunikaty-espi/art{id}-{issuer}-{title}` | `i.gremicdn.pl/file/{hash}/{name}.pdf` | monthly sitemaps `/sitemaps/sitemap.YYYYMM.xml` (≈1,500 ESPI pages per month, back to 2015; some months sparse; **no ESPI copies after 2026-10-26**) | allows report pages and sitemaps; disallows search and infinite scroll | ✅ PDF downloaded, text extractable |
| Bankier (bankier.pl) | `/wiadomosc/{ISSUER}-{title}-{id}.html` | `bonnier.pl/static/att/emitent/YYYY-MM/{name}.pdf` | `/gielda/wiadomosci/komunikaty-spolek/{n}` (34 per page) and per company `/gielda/notowania/akcje/{TICKER}/komunikaty` | allows report pages and listings; no robots.txt on bonnier.pl | ✅ PDF downloaded, text extractable (some pre-2024 links return 404) |
| BiznesRadar (biznesradar.pl) | `/a/{id},{issuer}-{number}-{title}` | `/storage/a/…/{name}.pdf` | `/temat/76,espi-ebi,{n}` | `Allow: /` | ✅ PDF downloaded, text extractable |
| StockWatch (stockwatch.pl) | | | | `Disallow: /*.pdf` | ⛔ not usable |
| financialreports.eu | | | | | ⛔ HTTP 403 |

- **What the PDFs contain:** the standard MAR Art. 19(1) notification form (name, position,
  issuer and LEI, then per transaction: instrument, nature, price(s) and volume(s),
  aggregated volume and price, date, venue). pdfplumber extracts it as text.
- **Terms of use:** all three portals forbid copying or republishing their own materials; none
  addresses automated reading. Only facts are extracted from the issuers' forms; PDFs are
  not stored or republished.
- **Matching:** a portal page matches a report when it was published within 10 minutes of
  the exchange timestamp, names the same issuer and has a similar title; BiznesRadar's
  report number must agree too. Order of preference: Parkiet, Bankier, BiznesRadar.
- **Coverage check (2026-11-09):** all 30 insider reports of 2026-10-19 … 11-06 were matched
  on Bankier and BiznesRadar, and the 15 up to 2026-10-26 on Parkiet as well; every one
  yielded a readable form.
- **Reach:** the Bankier and BiznesRadar listings are paged back about three weeks; older
  months rely on Parkiet's sitemaps.
- **Timestamps:** Parkiet's sitemap renders the Warsaw publication time as if it were UTC
  (`16:27+02:00` for 14:27 Warsaw); its UTC reading is used.

## 10. xtb.com stock pages (links only)

Event pages link to the company's page on XTB when one exists. Nothing is read from XTB
except whether the page exists and which ISIN it shows. Checked on 2026-11-11:

- **Discovery:** `https://www.xtb.com/pl/sitemap/instruments/cashstocks.xml` is a sitemap
  index; its files list about 7,500 stock pages as `/pl/akcje/<slug>`. Polish stocks use the
  company's name (`hydrotor`, `cd-projekt`, `pzu`) or the ticker with `-pl` (`s2b-pl`);
  foreign ones carry a country suffix (`abt-uk`).
- **Verification:** an unknown slug answers 301 to the stock list. Each page shows the
  instrument's ISIN, so a link is kept only when the slug is in the sitemap, the page does
  not redirect, and it shows the stock's ISIN. A wrong guess is never published.
- **robots.txt** disallows only `/liquidity`, `/front-api/*` and some query strings; the
  sitemaps and `/pl/akcje/` pages are allowed.
- **Load:** the nightly run checks only stocks of events from the last 45 days that are new
  or were last checked over 30 days ago, at most 40 per night, one request per second.
  Results are in `state/xtb_links.parquet`; `undercurrent xtb-links` checks a backlog.
- **Logos** (since 2026-11-11). Each verified page's title names XTB's symbol (`PKO.PL`), and
  XTB serves the company's logo at `https://logos.xtb.com/<code>_pl.svg` (403 when it has
  none). Since 2026-11-15 the icons are fetched for every listed stock and served from the
  site itself (§ 14).
- **Slug matching** ignores hyphens (`PKOBP` finds `pko-bp`) and also tries the first word
  of the company name (`santander`). Every guess is still accepted only on the ISIN.
- Undercurrent is not affiliated with XTB; the site says so next to every link.

## 11. filings.xbrl.org (ESEF annual reports)

Fiscal 2024 on come from bankier.pl's copies instead (§ 15).

Since 2021 issuers on EU regulated markets file their annual reports in ESEF: XHTML with
every number of the financial statements tagged in iXBRL. The exchange's copy is an ESPI
attachment, which gpw.pl serves only behind bot protection (§ 4), and the periodic-report
page itself holds no figures (checked on 2026-11-12 on two quarterly reports). XBRL
International's public repository converts each filing to xBRL-JSON. Checked on 2026-11-12:

- **Listing:** `https://filings.xbrl.org/api/filings?filter[country]=PL&page[size]=200&sort=date_added`
  (JSON:API, `links.next` for paging). Each filing gives its LEI (in `fxo_id`), the fiscal
  period end, the time it was added, and `json_url`.
- **Document:** `json_url` is the xBRL-JSON conversion, about 2.6 MB per annual report, mostly
  narrative text blocks. Facts carry `concept`, `entity`, `period` (an instant, or
  `start/end` with the end given as the next day's midnight) and `unit`.
- **Coverage:** 876 Polish filings from 313 entities; fiscal years 2020 (1), 2021 (130),
  2022 (302), 2023 (161), 2024 (6); nothing added after July 2024. It is history, not a live
  feed.
- **robots.txt** does not exist (404); the API needs no key.
- **Load:** `undercurrent esef --max N` reads at most N filings it has not read before (one
  request per second), keeps only the headline facts listed in `esef.CONCEPTS`, and never
  caches the documents. `esef.yml` runs it by hand and commits the facts.

## 12. api.gleif.org (LEI → ISIN)

The filings name their issuer by LEI; the lake knows ISINs. GLEIF's public API maps one to
the other: `https://api.gleif.org/api/v1/lei-records/<LEI>/isins` lists every ISIN linked to
the LEI (shares and bonds alike; the join with prices picks the share). Checked on
2026-11-12 (GRODNO: `PLGRODN00015`, the ISIN the GPW archive uses). Called once per new LEI;
results in `state/lei_isins.parquet`.

## 13. gpw.pl/wskazniki (market indicators, main market)

`https://www.gpw.pl/wskazniki` is the exchange's own table of market indicators: for every
main-market company (domestic and foreign tables), the ISIN, ticker, sector code with its
name in the cell's `title`, the number of shares issued, market value and book value in
millions of złoty, the date and type (`H` or `J`) of the latest financial report, P/BV, P/E
(`x` for a loss) and dividend yield (`--` for none). Plain HTML, no bot protection; checked
on 2026-11-15 (402 companies; 11BIT: `PL11BTS00015`, 2,417,199 shares, the same ISIN the
quote archive uses).

- Only the current table is free; the exchange sells its history ("Dane przetworzone i
  wskaźniki"). The nightly run lands it once per session, the evening of that session
  (`indicators.py`), so the history starts with the first run and is point in time.
- The page does not say which session its prices are from. Undercurrent keeps the shares,
  the book value and the earnings the table implies (market value ÷ P/E), and computes
  every ratio itself from its own close.
- One request a night, like the price files. Before relying on it beyond these figures,
  the exchange's terms for its information services (*Regulamin świadczenia usług
  informacyjnych*) should be checked; the site shows derived ratios, not the table.

## 14. Stock icons (logos.xtb.com, exchange company cards, company websites)

Every page that names a stock shows its icon: the stock page, the screener, the market
page's movers, the report feed, event pages and the stock of the week (`logos.py`). Checked
on 2026-11-15:

- **The exchange code.** The quote archive names main-market stocks by their short name
  (`11BIT`), not their code (`11B`). GPW's company card answers with both, and the company's
  website, at `https://www.gpw.pl/ajaxindex.php?start=infoTab&format=html&action=GPWListaSp&gls_isin=<ISIN>&lang=PL`
  (2.6 kB, plain HTML: "Skrót: 11B", "Strona www: www.11bitstudios.pl"). NewConnect's card,
  `https://newconnect.pl/spolka?isin=<ISIN>`, shows "Nazwa … (MLM)" and "Strona WWW", and
  writes some links as `http://https://…`; the NewConnect price file has the code already.
- **XTB's logo** for that code, `https://logos.xtb.com/<code>_pl.svg`: a 40×40 SVG tile on a
  light grey square, 403 when XTB has none. Where `xtb.py` verified XTB's page, its symbol
  is tried first. A few tiles embed a bitmap with hundreds of kilobytes of metadata
  (`dnp_pl.svg`, 823 kB); the bitmap is re-encoded (4 kB).
- **Else the company's own icon**: the home page's `apple-touch-icon`, else its largest
  `icon` link, else `/favicon.ico`; at least 32 px, scaled to a 64 px PNG. Company sites get
  15 seconds and no retries.
- **Kept:** SVGs without scripts, event handlers or foreign objects, at most 60 kB; PNGs.
  Files in `state/logos/<ISIN>.<svg|png>`, with `state/logos.parquet` (code, website, source,
  source URL, SHA-256, checked at). The site copies them to `logos/`, so no page loads
  anything from another server. A stock without an icon gets a coloured monogram.
- **Load:** the `logos` workflow, weekly on Sunday morning: new listings, stocks with an
  icon after 90 days and without one after 30; about two requests a stock, one a second.
- The logos are the companies' trademarks, shown only to identify the stock; the stock of
  the week page says so. XTB's terms for reusing its files are not stated; if XTB objects,
  dropping the first source leaves the companies' own icons and monograms.

## 15. Annual reports in ESEF from bankier.pl (fiscal 2024 on)

Since 2026-11-17 a supplement: the figures come from the exchange's own report pages
(§ 16), and Bankier's files add only the lines that form lacks (capex, depreciation,
current assets). The weekly run goes on if this step fails.

filings.xbrl.org has no Polish filing added after July 2024 (§ 11), so its newest annual
reports are fiscal 2022–2023. bankier.pl republishes every periodic report as a
"Wyniki finansowe" page and hosts its attachments, the annual report's ESEF files among them
(`esef_bankier.py`). Checked on 2026-11-17:

- **Finding the report.** Each company's announcements, newest first:
  `https://www.bankier.pl/gielda/notowania/akcje/<GPW short name>/komunikaty`, then `/2`,
  `/3`… Recent annual reports are titled "Wyniki finansowe SRR /2025" (consolidated) or
  "RR /2025" (standalone); Bankier's older pages are titled just "wyniki finansowe" and say
  "Raport roczny RR 2024" in their text. Per year the consolidated report is read, else the
  standalone one.
- **The file.** On `bonnier.pl/static/att/emitent/<yyyy-mm>/…`: the ESEF package
  (`gpw-2025-12-31-1-pl.zip`, or `.xbri`), otherwise an XHTML named the ESEF way. Some
  issuers attach only a rendered copy without XBRL tags (11 bit studios' 2025 report), which
  gives nothing.
- **Identity.** The report's LEI must belong to the company: GLEIF lists the ISIN, or GLEIF
  lists no listed share at all (it often lists only bonds; GPW's own LEI lists 15 bond ISINs
  and no share), and then the link is added. A report whose LEI GLEIF ties to another listed
  share is refused.
- **Coverage.** A sample of 30 random main-market companies: fiscal 2025 statements for 17;
  fiscal 2024 for 1, because Bankier no longer serves most files of March 2025 (404).
- **Point in time.** `known_at` is the report's publication time on Bankier, close to the
  issuer's own.
- **Load.** About a dozen pages a company and one package (1–30 MB, read in memory, never
  stored), one request a second: the `annual-reports` workflow looks up 120 companies a
  week and each again after 30 days.

## 16. Periodic reports' selected financial data (gpw.pl report pages)

Every annual (`RR`, `SRR`), half-year (`P`, `PS`, `PSr`) and quarterly (`Q`, `QS`, `QSr`)
report an issuer sends through ESPI is shown on its gpw.pl report page (§ 3) as the filled-in
form, not only as attachments. Besides the title sheet the form has the sheet
**"WYBRANE DANE FINANSOWE"**: the issuer's selected figures for the period and the prior
one, in the report currency and in euros. The page is the same plain HTML as any report
page; the attachments (§ 4) are not needed and not fetched (`gpw_importer.py`). Checked on
2026-11-17:

- **The listing's kind label is unreliable for periodic reports.** OPTEAM's annual report
  for 2024 is listed as "Bieżący", the one for 2025 as "Kwartalny"; some annual reports have
  no kind at all (`30-04-2026 23:57:29 | ESPI | /2025`), and those rows were dropped by the
  listing parser until 2026-11-17 (now kept as "Okresowy"). A per-type query (`typeRaports[]=R`)
  returns almost none of them. What is reliable: a periodic report has **no running number**
  (`/2025`, where a current report is `12/2025`), and the form code on its title sheet.
- **Finding them.** The rows of `bronze/reports` without a running number (2,096 main-market
  rows since 2024-01-01 in the local copy). For companies with no annual report read for the
  last fiscal year, the company card's ESPI tab:
  `https://www.gpw.pl/ajaxindex.php?start=reportsTab&type=e&action=GPWListaSp&gls_isin=<ISIN>&format=html&lang=PL&offset=<n>`,
  five reports a page whatever `limit` says, newest first, each titled by its form code
  (`RR`, `QSr`, `P`) or, for current reports, by their subject.
- **The title sheet** names the form, the fiscal year, the period ("obejmujący okres od
  2025-01-01 do 2025-12-31"; some forms leave it blank, then the sheet's header gives it,
  e.g. "3 kwartał(y) narastająco / 2026 okres od 2026-01-01 do 2026-09-30") and the
  currency ("w walucie PLN"). A quarterly form's figures are cumulative from the start of
  the fiscal year.
- **The sheet.** Rows of label + four figures: period, prior period, then both in euros.
  Labels are the issuer's own words (Polish, or English for some foreign issuers) and are
  mapped to the concepts of `esef.CONCEPTS`; units come from the header ("w tys. PLN",
  "w mln PLN") or a line inside the sheet ("mln PLN"). Brackets render as underscores, also
  around negative figures (`_14 937_` is −14 937). Consolidated reports often add the parent
  company's figures below the group's; only the group's are read.
- **Checks** (`gpw_importer.check`). A report is kept only if all pass; otherwise nothing of
  it is kept and `state/gpw_reports` says why (`status = failed_checks`, `checks`):
  the złoty and euro columns agree at an exchange rate of 3.7–5.2; liabilities + equity is
  never above total assets and at most 15 % short of them (provisions a form leaves out of
  "zobowiązania"; 50 % when it gives only the parent's equity, the rest being minorities);
  no part exceeds its total; total assets are at least 100 000; net profit ÷ shares gives
  the form's EPS; continuing plus discontinued profit gives the net profit; revenue is not
  negative. Where profit ÷ shares contradicts the EPS, the exchange's own share count
  (`gpw.pl/wskazniki`, § 13) decides whether the share count or the money is off by a
  thousand; without it the report is refused rather than guessed.
- **Accuracy, measured 2026-11-17** (`undercurrent gpw-reconcile`): 120 annual reports for
  fiscal 2025 against the same companies' ESEF statements from Bankier, line by line. Of
  253 comparisons where both read the same report (total assets within 1 %), 248 agree
  within 1 %: assets, liabilities, equity, operating profit, profit before tax 100 %;
  net profit 96 %, operating cash flow 96 %, revenue 96 % (100 % within 5 %), EPS 90 %.
  Each remaining difference is in the documents themselves, not in the reading: three
  where the issuer's form says something other than its audited statements (Celon's net
  profit 4 309 against 3 409, Captor's operating cash flow −4.4 m against −37.3 m,
  Stigroup's revenue 1.8 % apart) and two ESEF files that tag EPS in grosze as złoty
  (Prochem, Mennica), where the form is right. A fourth typo (ASMGR's net
  profit, which its own lines contradict) is refused by the checks.
- **Coverage.** 200 report pages of 2025–2026 read live: 193 periodic, 180 kept (assets
  92 %, net profit 88 %, revenue 79 %, operating cash flow 91 %, EPS 86 %); 5 had an empty
  sheet ("dane w załączeniu"), 4 gave no period anywhere, 4 failed their checks (2 with
  columns that are not złoty and euros, 2 whose balance sheet does not add up). The form
  has no capex or depreciation: those come from ESEF where available (§ 11, 15).
- **Alarms.** Each run reads one known report again (OPTEAM's for 2025, `CANARY`) and
  compares it with its known figures; the run fails when it reads wrong, when pages are
  read but no form is recognised, when under 60 % of periodic reports pass, or when over a
  quarter of pages fail to load. A page that fails is retried on the next runs, three times
  at most; an error or maintenance page is never taken for a report. When the parser
  changes (`PARSER_VERSION`), reports are read again and only the newest reading counts.
- **Point in time.** `known_at` is the report's publication on the exchange.
- **Not covered.** NewConnect's periodic reports are EBI files without this form. ESPI has no
  weekly or monthly issuer reports.
- **Load.** One request a report (~120 kB), one a second; a page is read once
  (`state/gpw_reports`), a company card at most once a month (`state/gpw_report_companies`).
