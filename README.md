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
pip install -r requirements.txt

# 4. Configure your API key
cp .env.example .env
# then edit .env and set GOOGLE_MAPS_API_KEY
```

## Usage

```bash
python -m src.cli input/sample_practices.csv --output output/enriched.csv

# validate inputs without spending API quota, with per-row logging:
python -m src.cli input/sample_practices.csv --dry-run --limit 5 --verbose
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

Input and output format (CSV vs. XLSX) is inferred from the file extension.
Progress is logged every 25 rows; the API key is never written to logs.

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

## Project structure

```
BEMI-website-enrichment/
├── README.md
├── requirements.txt
├── pyproject.toml            # project metadata + pytest config
├── .env.example              # template for your local .env
├── input/
│   └── sample_practices.csv  # example input
├── output/                   # generated outputs (gitignored)
└── src/
    ├── cli.py                # argument parsing + entry point
    ├── config.py             # env loading, column schema, constants
    ├── io.py                 # load/write CSV & XLSX
    ├── normalize.py          # phone / city / state normalization
    ├── google_places.py      # GooglePlacesClient (Places API New)
    ├── cache.py              # SQLite response cache
    ├── website_verify.py     # optional homepage verification
    ├── excel_format.py       # XLSX styling for human review
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
(`tests/test_excel_format.py`), and the enrichment/CLI orchestration with a
fake client (`tests/test_enrich.py`). `pyproject.toml` sets `pythonpath` so
`import src` works without installing the package.

## Roadmap

Implemented: CLI (CSV/XLSX, `--limit`/`--dry-run`/`--no-details`/
`--verify-websites`/`--verbose`, SQLite response cache), normalization, the
Google Places API (New) client (`text_search` / `place_details`) with
retry/backoff on 429, 5xx, and transient network errors, rule-based scoring
with a `high`/`medium`/`low`/`none` confidence, optional homepage
verification, review-formatted XLSX output, and the end-to-end pipeline that
scores every candidate, picks the best, completes it via Place Details, and
re-scores. Still to do:

- [ ] Tune the scoring weights/thresholds against labeled data.
- [ ] Per-request rate limiting / throttling for API calls.
- [ ] Use `locationBias`/`locationRestriction` to focus searches by city/state.

## Configuration

Environment variables (set in `.env`, see [`.env.example`](.env.example)):

| Variable                   | Default | Description                                   |
| -------------------------- | ------- | --------------------------------------------- |
| `GOOGLE_MAPS_API_KEY`      | —       | Google Maps Platform API key.                 |
| `ENRICH_REGION`            | `US`    | Default region for phone parsing.             |
| `ENRICH_REVIEW_THRESHOLD`  | `0.75`  | Confidence below which a row needs review.    |
```
