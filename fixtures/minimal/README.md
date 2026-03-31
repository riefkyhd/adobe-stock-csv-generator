# Minimal Fixture Set

This directory contains a tiny fixture image for contributor validation and onboarding.

- `sample_1x1.png`: tiny valid image file used to test scan/resume/output plumbing.

Example dry-run:

```bash
python3 src/adobe_stock_csv_cli.py \
  --portfolio-dir fixtures/minimal \
  --output-dir /tmp/adobe-stock-csv-fixture-run \
  --limit 1 \
  --dry-run
```

Use real image sets for meaningful metadata quality evaluation.
