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
- Undercurrent is not affiliated with XTB; the site says so next to every link.
