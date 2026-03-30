# Adobe Stock CSV Generator

A resumable local CLI that scans your image library and generates Adobe Stock CSV metadata safely, one image at a time.

## Features

- Incremental workflow: analyze `1 image -> validate -> append 1 CSV row -> flush`.
- Resume support: skips filenames already present in output CSV.
- Deterministic scan order for consistent reruns.
- Strict CSV contract for Adobe upload format.
- Failure isolation: invalid/unavailable analysis logs to review file and batch continues.
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
- `Keywords` required, max 49, duplicate normalization check.
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
2. Load a vision-capable model (currently using `google/gemma-3-4b`).
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
  --lmstudio-model google/gemma-3-4b \
  --portfolio-dir Portfolio \
  --output-dir output \
  --limit 5
```

6. Run full batch:

```bash
python3 src/adobe_stock_csv_cli.py \
  --backend lmstudio \
  --lmstudio-host http://127.0.0.1:1234 \
  --lmstudio-model google/gemma-3-4b \
  --portfolio-dir Portfolio \
  --output-dir output
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
  --output-dir output \
  --validate-only \
  --validate-lines 5
```

## Outputs

Generated under `output/`:

- `adobe_stock_upload.csv` (main upload CSV)
- `review_needed.csv` (rows that failed analysis/validation)
- `progress.json` (latest run counters/state)
- `run.log` (timestamped event log)

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
