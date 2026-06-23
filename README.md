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
| `-v, --verbose`  | Verbose, per-row logging.                                             |
| `--region CODE`  | Default region for phone parsing (ISO 3166, e.g. `US`).               |

Input and output format (CSV vs. XLSX) is inferred from the file extension.
Progress is logged every 25 rows; the API key is never written to logs.

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
| `google_business_status`   | e.g. `OPERATIONAL`, `CLOSED_PERMANENTLY`.               |
| `match_score`              | Numeric score, `0`–`100`.                               |
| `match_confidence`         | Confidence label: `high` / `medium` / `low` / `none`.   |
| `match_reason`             | Human-readable explanation incl. the numeric score.     |
| `needs_review`             | `True` unless the match is high-confidence and verified.|
| `error`                    | Error message if the row could not be processed.        |

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
(`tests/test_google_places.py`), and the enrichment/CLI orchestration with a
fake client (`tests/test_enrich.py`). `pyproject.toml` sets `pythonpath` so
`import src` works without installing the package.

## Roadmap

Implemented: CLI (CSV/XLSX, `--limit`/`--dry-run`/`--no-details`/`--verbose`),
normalization, the Google Places API (New) client (`text_search` /
`place_details`) with retry/backoff on 429, 5xx, and transient network errors,
rule-based scoring with a `high`/`medium`/`low`/`none` confidence, and the
end-to-end pipeline that scores every candidate, picks the best, completes it
via Place Details, and re-scores. Still to do:

- [ ] Tune the scoring weights/thresholds against labeled data.
- [ ] Per-request rate limiting and basic caching for API calls.
- [ ] Use `locationBias`/`locationRestriction` to focus searches by city/state.

## Configuration

Environment variables (set in `.env`, see [`.env.example`](.env.example)):

| Variable                   | Default | Description                                   |
| -------------------------- | ------- | --------------------------------------------- |
| `GOOGLE_MAPS_API_KEY`      | —       | Google Maps Platform API key.                 |
| `ENRICH_REGION`            | `US`    | Default region for phone parsing.             |
| `ENRICH_REVIEW_THRESHOLD`  | `0.75`  | Confidence below which a row needs review.    |
```
