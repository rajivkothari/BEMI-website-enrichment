# BEMI Website Enrichment

A command-line tool that takes a spreadsheet of businesses/practices and
populates their likely official websites using the
[Google Maps Platform Places API](https://developers.google.com/maps/documentation/places/web-service/overview).

For each row it searches Google Places by name + location, picks the best
candidate, fetches its details (website, phone, address), and scores how
confident the match is — flagging low-confidence rows for manual review.

> **Status:** Skeleton. The CLI, file I/O, normalization, and scoring are
> implemented and tested. The live Google Places API calls are stubbed
> (`google_places.search_text` / `get_place_details` raise
> `NotImplementedError`); see [Roadmap](#roadmap). Running the tool today
> produces an output file with every row flagged `needs_review` and an
> `error` of `not_implemented: ...`, which makes the end-to-end pipeline easy
> to verify before the API work lands.

## Requirements

- Python 3.11+
- A Google Maps Platform API key with the **Places API (New)** enabled
  (only needed once the integration is implemented)

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
python -m src.cli --input input/sample_practices.csv --output output/enriched.csv
```

Options:

| Flag             | Description                                                   |
| ---------------- | ------------------------------------------------------------- |
| `-i, --input`    | Path to the input CSV or XLSX (required).                     |
| `-o, --output`   | Path to write the enriched CSV or XLSX (required).            |
| `--limit N`      | Only process the first `N` rows (handy for testing).          |
| `--region CODE`  | Default region for phone parsing (ISO 3166, e.g. `US`).       |

Input and output format (CSV vs. XLSX) is inferred from the file extension.

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

| Column                     | Description                                              |
| -------------------------- | ------------------------------------------------------- |
| `practice_name`            | From input.                                             |
| `phone`                    | From input.                                             |
| `city`                     | From input.                                             |
| `state`                    | From input.                                             |
| `google_place_id`          | Google Place ID of the matched place.                   |
| `google_name`              | Place name as returned by Google.                       |
| `google_formatted_address` | Formatted address from Google.                          |
| `google_phone`             | Phone number from Google.                               |
| `google_website`           | Website URL from Google (the primary goal).             |
| `match_confidence`         | Confidence score, `0.0`–`1.0`.                          |
| `match_reason`             | Short, human-readable explanation of the score.         |
| `needs_review`             | `True` when confidence is below the review threshold.   |
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
    ├── google_places.py      # Places API helpers (stubbed)
    ├── scoring.py            # match confidence scoring
    └── enrich.py             # orchestration (row -> enriched row)
```

## Development

Run the test suite:

```bash
pytest
```

The tests cover the normalization helpers (`tests/test_normalize.py`) and the
match scoring (`tests/test_scoring.py`). `pyproject.toml` sets `pythonpath`
so `import src` works without installing the package.

## Roadmap

This skeleton is intentionally scoped to the plumbing. Still to do:

- [ ] Implement `google_places.search_text` (Places API **Text Search**) with
      an appropriate field mask.
- [ ] Implement `google_places.get_place_details` (Places API **Details**).
- [ ] Candidate selection: rank/choose the best result instead of the first.
- [ ] Tune `scoring.score_match` (weighting, address/city/state agreement,
      website sanity checks).
- [ ] Rate limiting, retries, and basic caching for API calls.

## Configuration

Environment variables (set in `.env`, see [`.env.example`](.env.example)):

| Variable                   | Default | Description                                   |
| -------------------------- | ------- | --------------------------------------------- |
| `GOOGLE_MAPS_API_KEY`      | —       | Google Maps Platform API key.                 |
| `ENRICH_REGION`            | `US`    | Default region for phone parsing.             |
| `ENRICH_REVIEW_THRESHOLD`  | `0.75`  | Confidence below which a row needs review.    |
```
