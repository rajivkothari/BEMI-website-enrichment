# BEMI Website Enrichment

A command-line tool that takes a spreadsheet of businesses/practices and
populates their likely official websites using the
[Google Maps Platform Places API](https://developers.google.com/maps/documentation/places/web-service/overview).

For each row it searches Google Places by name + location, picks the best
candidate, fetches its details (website, phone, address), and scores how
confident the match is — flagging low-confidence rows for manual review.

> **Status:** Functional end-to-end. The CLI, file I/O, normalization,
> scoring, the Google Places API (New) client, and the full enrichment
> pipeline (score every candidate → pick best → complete via Place Details →
> re-score) are implemented and tested. Scoring weights are a baseline; see
> [Roadmap](#roadmap).
>
> The Places integration requires the **Places API (New)** to be enabled on
> your Google Cloud project. If it is not, every row's `error` column will
> contain a clear `HTTP 403 ... Places API (New) has not been used in
> project ... or it is disabled` message with a link to enable it.

## Requirements

- Python 3.11+
- A Google Maps Platform API key with the **Places API (New)** enabled
  ([enable it here](https://console.cloud.google.com/apis/library/places.googleapis.com))

## Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd BEMI-website-enrichment

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt        # or: make install

# 4. Configure your API key
cp .env.example .env
# then edit .env and set GOOGLE_MAPS_API_KEY
```

Common tasks are also wrapped in a `Makefile`:

```bash
make install   # pip install -r requirements.txt
make test      # run the test suite (no network)
make sample    # enrich input/sample_practices.csv -> output/sample_enriched.xlsx
```

## Usage

```bash
# Basic: enrich a CSV (or XLSX), inferring format from the extension.
python -m src.cli input/sample_practices.csv --output output/enriched.csv

# Validate inputs without spending API quota (no key needed), per-row logging.
python -m src.cli input/sample_practices.csv --dry-run --limit 5 --verbose

# Highest confidence: verify each homepage and emit a styled XLSX for review.
python -m src.cli input/sample_practices.xlsx --output output/enriched.xlsx --verify-websites

# Large file: checkpoint every 100 rows; rerun the same command with --resume
# if it is interrupted.
python -m src.cli input/big.xlsx --output output/big.enriched.xlsx --checkpoint-every 100 --resume

# Also emit Bullseye-compatible payloads (JSON Lines).
python -m src.cli input/practices.csv --output output/enriched.csv \
    --bullseye-json output/bullseye_payload.jsonl
```

The input is a positional argument. Options:

| Flag             | Description                                                            |
| ---------------- | --------------------------------------------------------------------- |
| `input`          | Path to the input CSV or XLSX (positional, required).                 |
| `-o, --output`   | Output path. Defaults to `output/<input-stem>.enriched<ext>`.         |
| `--limit N`      | Only process the first `N` rows.                                      |
| `--dry-run`      | Normalize + build queries but make no API calls and write no output.  |
| `--no-details`   | Skip the Place Details lookup (faster/cheaper; may miss some sites).  |
| `--verify-websites` | Fetch each matched homepage and verify the phone/city/state on it (off by default; adds time). |
| `-v, --verbose`  | Verbose, per-row logging.                                             |
| `--region CODE`  | Default region for phone parsing (ISO 3166, e.g. `US`).               |
| `--cache-db PATH`| SQLite cache file (default `.cache/enrichment_cache.sqlite`).         |
| `--no-cache`     | Disable the response cache (always call the API).                     |
| `--cache-ttl-days N` | Refresh cached entries older than N days (default 30).           |
| `--resume`       | Resume from an existing output file: skip rows that already have a `google_place_id` or `error`. |
| `--checkpoint-every N` | Write partial output every N enriched rows (default 50; 0 disables). |
| `--bullseye-json PATH` | Also write Bullseye-compatible enrichment payloads (JSON Lines). |

Input and output format (CSV vs. XLSX) is inferred from the file extension.
Progress is logged every 25 rows; the API key is never written to logs.

### Resuming large files

Runs checkpoint the **full** output every `--checkpoint-every` rows (default
50), and writes are atomic (temp file + rename) so a partial file is always a
valid CSV/XLSX. If a run is interrupted, rerun the same command with
`--resume`: rows that already have a `google_place_id` or `error` are kept
as-is (no API call), and only the remaining rows are processed.

```bash
python -m src.cli input/practices.xlsx --output output/enriched.xlsx --resume --checkpoint-every 25
```

For very large inputs, raise `--checkpoint-every` (e.g. `500`) to reduce how
often the full file is rewritten. Note: errored rows are treated as done and
skipped on resume; delete the output (or clear their `error`) to retry them.

### Caching

Google Places responses are cached in a local SQLite database
(`--cache-db`, default `.cache/enrichment_cache.sqlite`) so re-running over
the same input doesn't re-call (or re-pay for) the API. Text-search results
are keyed by the normalized query and place details by Place ID; entries
older than the TTL (`--cache-ttl-days`, default 30) are refreshed
automatically. Use `--no-cache` to bypass it. The cache stores only public
Places data — never the API key.

### Website verification (optional)

With `--verify-websites`, each matched homepage is fetched (redirects
followed, homepage only — no crawling) and checked for the input phone, city,
and state. Matches add to the score (phone `+20`, city `+10`, state `+5`) and
are summarized in `verification_notes`. It's off by default because it adds a
network request per row. A site that can't be fetched never fails the row —
the reason is recorded in `verification_notes` and no bonus is applied.

## Web UI (Streamlit)

A branded, local web UI is provided for the upload → review → export flow.

**One-command launcher** (creates the venv + installs deps on first run, then
opens the app in your browser):

- **Windows:** double-click `start-web.bat`, or run `.\start-web` in PowerShell.
- **macOS / Linux:** `./start-web.sh`

Manual equivalent:

```bash
pip install -r requirements.txt   # includes streamlit   (or: make install)
streamlit run app.py              # opens in your browser (or: make web)
```

It reuses the same pipeline (`src/enrich.py`, `src/bullseye_export.py`) and:

- **Ingests CSV/XLSX, including Outscraper exports** — columns are auto-detected
  (`name`→practice_name, `website`, `place_id`, …) and remappable.
- **"Only look up rows missing a website"** (default on): rows that already have
  a website (e.g. from Outscraper) are kept and cleaned (tracking junk like
  `%3Futm_source%3D…` is stripped); only the gaps hit Google — so you don't pay
  to re-find sites you already have.
- A **pre-run cost estimate** (rows · paid Google lookups · est. $) — only rows
  that actually call the API are counted; tune `$/lookup` to your billing.
- An editable **review grid** with the website + phone prominent, color-coded
  confidence, and per-row `approved`/`rejected`/`replaced` decisions.
- One-click **Cleaned XLSX** and **Bullseye JSONL** downloads.

The UI needs `GOOGLE_MAPS_API_KEY` in `.env` only to look up the missing-website
rows; everything else (mapping, cleaning, review, export) works without it.

### Web-search fallback (optional)

Google **Places** sometimes has no website for a listing even when the practice
has one (it shows up only in a normal web search). With a web-search provider
configured, rows that Places can't resolve are searched on the web, and a result
is accepted **only if it's a standalone, name-matching domain** — directory
sites (Healthgrades, Zocdoc, US News, …) are skipped, and a group/parent site
(e.g. a multi-provider practice) is recorded as a review *candidate* rather than
auto-accepted. Configure one provider in `.env` (`SERPER_API_KEY` for Google
results, or `BRAVE_API_KEY`); see [`.env.example`](.env.example).

## Input format

The input spreadsheet must contain these columns:

| Column          | Description                          |
| --------------- | ------------------------------------ |
| `practice_name` | Business / practice name.            |
| `phone`         | Phone number (any common format).    |
| `city`          | City.                                |
| `state`         | US state (full name or abbreviation).|

A ready-to-use example lives at [`input/sample_practices.csv`](input/sample_practices.csv).

## Output format

The output preserves the input columns and appends the enrichment results:

Any original input columns are preserved, followed by:

| Column                     | Description                                              |
| -------------------------- | ------------------------------------------------------- |
| *(original columns)*       | Echoed from the input (e.g. `practice_name`, `phone`…). |
| `normalized_phone`         | Input phone in E.164 (e.g. `+14155550182`), or blank.   |
| `google_place_id`          | Google Place ID of the matched place.                   |
| `google_name`              | Place name as returned by Google.                       |
| `google_formatted_address` | Formatted address from Google.                          |
| `google_phone`             | Phone number from Google.                               |
| `google_website`           | Website URL from Google (the primary goal).             |
| `google_maps_uri`          | Google Maps link for the place (clickable in XLSX).     |
| `google_business_status`   | e.g. `OPERATIONAL`, `CLOSED_PERMANENTLY`.               |
| `official_website_candidate` | `google_website` unless it's a directory/social site (then blank). |
| `website_source`           | `google_places` or `google_places_verified` (homepage confirmed). |
| `match_score`              | Numeric score, `0`–`100`.                               |
| `match_confidence`         | Confidence label: `high` / `medium` / `low` / `none`.   |
| `match_reason`             | Human-readable explanation incl. the numeric score.     |
| `needs_review`             | `True` unless the match is high-confidence and verified.|
| `verification_notes`       | Homepage-verification summary (with `--verify-websites`).|
| `error`                    | Error message if the row could not be processed.        |
| `review_decision`          | **Blank for the reviewer** — `approved`/`rejected`/`replaced`. |
| `reviewer_notes`           | **Blank for the reviewer** — free-text notes.           |

When the output path ends in `.xlsx`, the sheet is formatted for review: a
frozen, filtered header; autofit column widths; `needs_review` rows
highlighted; `match_confidence` color-coded (green/yellow/orange/red for
high/medium/low/none); and clickable `google_website` / `google_maps_uri` /
`official_website_candidate` links.

## Recommended review workflow

Export to `.xlsx` (`--output reviewed.xlsx`), ideally with `--verify-websites`,
then in the spreadsheet:

1. **Filter `needs_review = TRUE`** — these are the rows that need a human.
2. **Check the `medium` / `low` confidence rows** (color-coded) — read
   `match_reason` and open the `official_website_candidate` link to confirm.
3. **Confirm directory/social URLs manually** — rows where
   `official_website_candidate` is blank but `google_website` is set point at a
   directory (Yelp, Facebook, …); find the real site if there is one.
4. **Fill `review_decision`** as `approved` / `rejected` / `replaced` (put the
   corrected URL in `reviewer_notes` when `replaced`).

High-confidence rows (green, `needs_review = FALSE`) generally need no action.

## Audit logs

Every (non-dry-run) run writes two artifacts next to the output file:

- **`run_log_{timestamp}.json`** — run metadata and summary counts: input/output
  files, `started_at`/`completed_at`, `row_count`, `enriched_count`,
  `high`/`medium`/`low_confidence_count`, `needs_review_count`, `errors_count`,
  `api_calls_estimated` (Places API calls actually sent), `cache_hits`,
  `cache_misses`, and the `flags` used.
- **`enrichment_events.csv`** — one row per input row: `row_index`,
  `practice_name`, `phone`, `city`, `state`, `selected_place_id`,
  `selected_website`, `score`, `confidence`, `needs_review`, `reason`, `error`
  (overwritten each run; the JSON log is timestamped, so it accumulates).

## Bullseye export

`--bullseye-json output/bullseye_payload.jsonl` additionally writes one
Bullseye-compatible enrichment payload per row as JSON Lines:

```json
{
  "lead_external_id": null,
  "practice_name": "...", "phone": "...", "city": "...", "state": "...",
  "website": "https://...",
  "website_confidence": "high",
  "website_evidence": {
    "source": "google_places",
    "google_place_id": "...", "google_maps_uri": "...",
    "matched_phone": true, "matched_city": true, "matched_state": true,
    "score": 92,
    "reason": "Exact phone match; city/state matched; website returned by Google Places."
  },
  "needs_manual_review": false
}
```

`website` is the vetted official site (blank for directory/social matches),
`source` is `google_places` or `google_places_verified`, and the `matched_*`
flags come from the Google place (and homepage verification, if run). This is
export-only — it does not connect to any Bullseye database or API.

## Project structure

```
BEMI-website-enrichment/
├── README.md
├── Makefile                  # install / test / sample / web
├── app.py                    # Streamlit web UI
├── start-web.bat / .sh       # one-command launcher (Windows / Unix)
├── .streamlit/config.toml    # Bullseye theme
├── requirements.txt
├── pyproject.toml            # project metadata + pytest config
├── .env.example              # template for your local .env
├── input/
│   └── sample_practices.csv  # example input (5 sample rows)
├── output/                   # generated outputs (gitignored)
└── src/
    ├── ui_support.py         # pure UI helpers (Outscraper mapping, etc.)
    ├── cli.py                # argument parsing + entry point
    ├── config.py             # env loading, column schema, constants
    ├── io.py                 # load/write CSV & XLSX
    ├── normalize.py          # phone / city / state normalization
    ├── google_places.py      # GooglePlacesClient (Places API New)
    ├── cache.py              # SQLite response cache
    ├── website_verify.py     # optional homepage verification
    ├── excel_format.py       # XLSX styling for human review
    ├── audit.py              # run log + per-row events
    ├── bullseye_export.py    # Bullseye JSONL payload format
    ├── scoring.py            # match confidence scoring
    └── enrich.py             # orchestration (row -> enriched row)
```

## Development

Run the test suite:

```bash
pytest
```

The tests cover normalization (`tests/test_normalize.py`), scoring
(`tests/test_scoring.py`), the Places client with mocked HTTP
(`tests/test_google_places.py`), the SQLite cache (`tests/test_cache.py`), website verification
(`tests/test_website_verify.py`), XLSX review formatting
(`tests/test_excel_format.py`), audit logging (`tests/test_audit.py`), the
Bullseye export (`tests/test_bullseye_export.py`), and the enrichment/CLI
orchestration with a fake client (`tests/test_enrich.py`). `pyproject.toml`
sets `pythonpath` so `import src` works without installing the package.

## Roadmap

Implemented: CLI (CSV/XLSX, `--limit`/`--dry-run`/`--no-details`/
`--verify-websites`/`--verbose`, SQLite response cache, `--resume` +
checkpointing with atomic writes), normalization, the Google Places API (New)
client (`text_search` / `place_details`) with retry/backoff on 429, 5xx, and
transient network errors, rule-based scoring with a `high`/`medium`/`low`/
`none` confidence, optional homepage verification, review-formatted XLSX
output, per-run audit logs, a Bullseye JSONL export, and the end-to-end
pipeline that scores every candidate, picks the best, completes it via Place
Details, and re-scores. Still to do:

- [ ] Tune the scoring weights/thresholds against labeled data.
- [ ] Per-request rate limiting / throttling for API calls.
- [ ] Use `locationBias`/`locationRestriction` to focus searches by city/state.

## Configuration

Environment variables (set in `.env`, see [`.env.example`](.env.example)):

| Variable                   | Default | Description                                   |
| -------------------------- | ------- | --------------------------------------------- |
| `GOOGLE_MAPS_API_KEY`      | —       | Google Maps Platform API key.                 |
| `ENRICH_REGION`            | `US`    | Default region for phone parsing.             |

The API key is read from the environment / `.env` only, is sent solely in the
`X-Goog-Api-Key` request header, and is never written to logs or output. Keep
`.env` out of version control (it is gitignored).

## Known limitations

This tool proposes likely official websites; it does not guarantee them.
Treat the output as a strong starting point for human review, not ground truth:

- **Google may return a parent organization** (e.g. a hospital network or
  franchise brand) instead of the specific practice you searched for.
- **Multi-location practices require review** — the matched location (and its
  website/phone) may not be the one in your row.
- **Directory/social URLs are not official websites.** Yelp, Facebook,
  Healthgrades, Zocdoc, etc. are flagged and left out of
  `official_website_candidate`; a real site may still need to be found.
- **Phone numbers can be reused or shared** across practices (answering
  services, billing offices, suites), so a phone match is suggestive, not
  proof.
- **Some practices have no standalone website** (only a directory listing or a
  parent-org page), so no official website will be found.
- **API usage may incur Google Maps Platform costs.** Text Search and Place
  Details are billable; the local cache (`--cache-db`) and `--dry-run` help
  keep usage (and spend) down.
