.PHONY: install test sample

# Install runtime + test dependencies.
install:
	python -m pip install -r requirements.txt

# Run the test suite (no network; uses mocked clients).
test:
	python -m pytest -q

# Enrich the bundled sample input -> output/sample_enriched.xlsx.
# Requires GOOGLE_MAPS_API_KEY in your environment or .env.
sample:
	python -m src.cli input/sample_practices.csv --output output/sample_enriched.xlsx
