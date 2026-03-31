# Adobe Stock CSV Generator

A resumable local CLI that scans your image library and generates Adobe Stock CSV metadata safely, one image at a time.

## Features

- Incremental workflow: analyze `1 image -> validate -> append 1 CSV row -> flush`.
- Resume support: skips filenames already present in output CSV.
- Deterministic scan order for consistent reruns.
- Strict CSV contract for Adobe upload format.
- Failure isolation: invalid/unavailable analysis logs to review file and batch continues.
- Built-in performance benchmarking per run (avg/p50/p95/max image latency + slowest file + throughput).
- Multiple analyzer backends:
  - `lmstudio` (default)
  - `ollama` (optional)
  - `openai` (optional)

## CSV Contract

Header is always:

```csv
Filename,Title,Keywords,Category,Releases
```

Rules enforced by the CLI:

- UTF-8 CSV, comma delimiter, standard quoting, LF newlines.
- `Title` required, no commas, max length guard.
- `Keywords` required; auto-cleaned by normalization, deduplicated, filtered for compliance, and capped at 49.
- Keyword targeting follows Adobe best practice: balanced `20-30` when justified, minimum accepted `15` after cleanup.
- Place names are generalized by default (avoid specific city/country names unless externally verified).
- Category can be delegated to a separate LM Studio model (default `google/gemma-3-4b`) for better classification stability.
- `Category` must be integer `1..21`.
- `Releases` must be blank unless explicitly verified.

## Project Structure

```text
adobe-stock-csv-generator/
├── docs/
├── prompts/
├── src/
│   └── adobe_stock_csv_cli.py
├── tests/
│   └── test_adobe_stock_csv_cli.py
├── AGENTS.md
└── README.md
```

Notes:

- `Portfolio/` is your local image library and is intentionally excluded from Git.
- `output/` contains generated run artifacts and is intentionally excluded from Git.

## Requirements

- Python 3.10+
- macOS/Linux terminal
- One analyzer backend:
  - LM Studio local server (recommended), or
  - Ollama local server, or
  - OpenAI API key

## Quick Start (LM Studio default)

1. Open LM Studio.
2. Load a vision-capable model (recommended primary: `qwen/qwen3-vl-8b`).
3. Start Local Server (`http://127.0.0.1:1234`).
4. Verify:

```bash
curl http://127.0.0.1:1234/v1/models
```

5. Run smoke test:

```bash
python3 src/adobe_stock_csv_cli.py \
  --backend lmstudio \
  --lmstudio-host http://127.0.0.1:1234 \
  --lmstudio-model qwen/qwen3-vl-8b \
  --lmstudio-fallback-model google/gemma-3-4b \
  --lmstudio-category-model google/gemma-3-4b \
  --lmstudio-timeout-seconds 120 \
  --lmstudio-max-tokens 420 \
  --lmstudio-top-p 0.9 \
  --lmstudio-top-k 40 \
  --portfolio-dir Portfolio \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b \
  --limit 5
```

6. Run full batch:

```bash
python3 src/adobe_stock_csv_cli.py \
  --backend lmstudio \
  --lmstudio-host http://127.0.0.1:1234 \
  --lmstudio-model qwen/qwen3-vl-8b \
  --lmstudio-fallback-model google/gemma-3-4b \
  --lmstudio-category-model google/gemma-3-4b \
  --lmstudio-timeout-seconds 120 \
  --lmstudio-max-tokens 420 \
  --lmstudio-top-p 0.9 \
  --lmstudio-top-k 40 \
  --portfolio-dir Portfolio \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b
```

## Optional Backends

### Ollama

```bash
ollama serve
ollama pull qwen3.5:2b-fast
python3 src/adobe_stock_csv_cli.py \
  --backend ollama \
  --ollama-model qwen3.5:2b-fast \
  --portfolio-dir Portfolio \
  --output-dir output
```

### OpenAI

```bash
export OPENAI_API_KEY="your_key"
export OPENAI_MODEL="gpt-4.1-mini"
python3 src/adobe_stock_csv_cli.py \
  --backend openai \
  --model "$OPENAI_MODEL" \
  --portfolio-dir Portfolio \
  --output-dir output
```

## Validation Command

Check CSV structure and print first lines:

```bash
python3 src/adobe_stock_csv_cli.py \
  --output-dir output/lmstudio/qwen-qwen3-vl-8b \
  --validate-only \
  --validate-lines 5
```

## Outputs

Generated under the selected output folder (recommended model-specific path):

- `adobe_stock_upload.csv` (main upload CSV)
- `review_needed.csv` (rows that failed analysis/validation)
- `progress.json` (latest run counters/state)
- `run.log` (timestamped event log)

Example:

- `output/lmstudio/qwen-qwen3-vl-8b/adobe_stock_upload.csv`

If `--backend lmstudio` is used with default `--output-dir output`, the CLI automatically writes to:

- `output/lmstudio/<model-slug>/...`

## Benchmarking

Each run tracks model benchmark metrics in both `progress.json` and `run.log`:

- average image time
- p50 and p95 image time
- max (slowest) image time and filename
- average/p95 analysis-only time
- throughput (images/minute)
- first-image warmup time

Use these metrics to compare models (for example, Qwen vs Gemma) on the same portfolio subset.

## LM Studio Tuning Notes

- For this metadata workflow, do **not** max context length (`262144`); use `2048-4096` for better speed/stability.
- The CLI can enforce API-side inference controls regardless of UI defaults:
  - `--lmstudio-timeout-seconds`
  - `--lmstudio-max-tokens`
  - `--lmstudio-top-p`
  - `--lmstudio-top-k`

## Testing

Run tests:

```bash
python3 -m unittest -q tests/test_adobe_stock_csv_cli.py
```

## Safety + Resume Behavior

- The CLI never rewrites the CSV on each iteration.
- Each successful row is appended and flushed immediately.
- Reruns are safe: existing filenames are skipped automatically.
- Failures are logged and do not stop the whole batch.

## Troubleshooting

- If LM Studio crashes when invoked by CLI tooling, open LM Studio manually and keep server running.
- If `curl http://127.0.0.1:1234/v1/models` fails, restart Local Server in LM Studio.
- If using LAN host and it fails but localhost works, LM Studio is likely bound to localhost only.
