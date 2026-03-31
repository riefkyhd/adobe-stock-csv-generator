# Contributing

Thanks for improving this project.

## Quick Start

1. Clone the repository.
2. Install locally:
   ```bash
   python3 -m pip install .
   ```
3. Run tests:
   ```bash
   python3 -m unittest -q tests/test_adobe_stock_csv_cli.py
   ```

## Local Validation

- Console entry point:
  ```bash
  adobe-stock-csv --help
  ```
- Direct script path:
  ```bash
  python3 src/adobe_stock_csv_cli.py --help
  ```

## Lightweight Fixture Flow

Use the tiny fixture set to verify wiring without a personal portfolio:

```bash
python3 src/adobe_stock_csv_cli.py \
  --portfolio-dir fixtures/minimal \
  --output-dir /tmp/adobe-stock-csv-fixture-run \
  --limit 1 \
  --dry-run
```

This validates scan/validation/resume plumbing. Metadata quality review still requires real images and human QA.

## Pull Request Expectations

- Keep changes scoped and explain behavior impacts.
- Add or update tests when behavior changes.
- Do not commit personal portfolio images or generated `output/` artifacts.
- Preserve the CSV contract:
  `Filename,Title,Keywords,Category,Releases`
