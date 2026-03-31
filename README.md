# Adobe Stock CSV Generator

A resumable local CLI that scans image files and builds Adobe Stock upload CSV metadata with a workflow designed for safety and restartability.

## Why This Repo

This project focuses on workflow safety, not one-shot AI generation:

- Row-by-row processing: `analyze 1 image -> validate -> append 1 row -> flush -> continue`
- Resume/recovery guarantees: reruns skip filenames already in CSV
- Strict validation before write: title/keywords/category/release checks enforced
- Review queue for uncertain/invalid rows: failures go to `review_needed.csv` without stopping batch
- Benchmark visibility: run-level latency/throughput stats in `progress.json` and `run.log`
- Deterministic scan order for reproducible reruns

## What This Repo Does Not Do

- It does **not** guarantee perfect metadata accuracy.
- It does **not** guarantee perfect category assignment for every image.
- It does **not** replace human review before Adobe submission.
- It does **not** bypass Adobe policy requirements.

Human review is still expected for final acceptance quality.

## Project Maturity

Current maturity signals:

- Unit tests for resume, validation, collisions, fallback behavior, and CSV structure checks
- CI runs tests plus packaging/install smoke checks
- Local+packaged CLI entry points supported
- Strict Adobe CSV contract enforcement

## CSV Contract

Header is always:

```csv
Filename,Title,Keywords,Category,Releases
```

Rules enforced by the CLI:

- UTF-8 CSV, comma delimiter, standard quoting, LF newlines
- `Title` required, no commas, max length guard
- `Keywords` cleaned/deduplicated/filtered and capped at 49
- Category must be numeric `1..21`
- `Releases` blank unless explicitly verified

## Installation

### Option A: Local install with pip

```bash
python3 -m pip install .
```

Then run:

```bash
adobe-stock-csv --help
```

### Option B: Isolated install with pipx (recommended for CLI usage)

```bash
pipx install .
```

Then run:

```bash
adobe-stock-csv --help
```

## Backward-Compatible Direct Script Usage

Direct script execution remains supported:

```bash
python3 src/adobe_stock_csv_cli.py --help
```

## Quick Start (LM Studio default)

1. Open LM Studio.
2. Load a vision-capable model (recommended primary: `qwen/qwen3-vl-8b`).
3. Start Local Server (`http://127.0.0.1:1234`).
4. Verify:

```bash
curl http://127.0.0.1:1234/v1/models
```

Smoke test:

```bash
adobe-stock-csv \
  --backend lmstudio \
  --lmstudio-host http://127.0.0.1:1234 \
  --lmstudio-model qwen/qwen3-vl-8b \
  --lmstudio-fallback-model google/gemma-3-4b \
  --lmstudio-category-model google/gemma-3-4b \
  --portfolio-dir Portfolio \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b \
  --limit 5
```

Full run:

```bash
adobe-stock-csv \
  --backend lmstudio \
  --lmstudio-host http://127.0.0.1:1234 \
  --lmstudio-model qwen/qwen3-vl-8b \
  --lmstudio-fallback-model google/gemma-3-4b \
  --lmstudio-category-model google/gemma-3-4b \
  --portfolio-dir Portfolio \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b
```

## Lightweight Fixture for Contributor Validation

A tiny fixture is included at `fixtures/minimal/` for onboarding and plumbing checks.

Example:

```bash
adobe-stock-csv \
  --portfolio-dir fixtures/minimal \
  --output-dir /tmp/adobe-stock-csv-fixture-run \
  --limit 1 \
  --dry-run
```

This is for wiring validation only. Use real image sets for metadata quality evaluation.

## Validate Existing CSV Structure

```bash
adobe-stock-csv \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b \
  --validate-only \
  --validate-lines 5
```

## Outputs

Generated under selected output dir:

- `adobe_stock_upload.csv`
- `review_needed.csv`
- `progress.json`
- `run.log`

## Optional Backends

- `lmstudio` (default)
- `ollama`
- `openai`

## Testing

```bash
python3 -m unittest -q tests/test_adobe_stock_csv_cli.py
```

## Internal Maintainer Docs

These remain public but are maintainer/internal workflow aids, not end-user runtime requirements:

- `AGENTS.md`
- `prompts/`

## License

MIT. See `LICENSE`.
