# WORKFLOW.md

## Recommended batch strategy

Use an **incremental, resumable** workflow.

### Why incremental is better

Do not analyze the entire folder first and write the CSV only at the end.

Instead:

1. read directory
2. sort files
3. load existing CSV
4. skip already processed filenames
5. analyze one image
6. validate metadata
7. append one row
8. flush file
9. continue

This is the most reliable strategy for long batches and matches Adobe CSV work better than a single giant one-shot run.

## Suggested runtime workflow

### Step 1 — Preflight scan

Build a file list from:

`/Users/administrator/Documents/Kiki/adobe_stock_csv_codex_kit/Portfolio`

Include common image extensions such as:

- `.jpg`
- `.jpeg`
- `.png`
- `.tif`
- `.tiff`
- `.webp`

Exclude:

- csv files
- hidden files
- thumbnails
- sidecar files
- exported previews that should not be submitted

### Step 2 — Load existing progress

If `output/adobe_stock_upload.csv` already exists:

- read all existing rows
- collect processed filenames into a set
- skip those files when iterating

### Step 3 — Analyze one image at a time

For each unprocessed image:

- inspect the image
- determine the main subject
- create a short factual title
- generate keywords ordered from general to specific, strongest first
- choose one numeric category
- leave releases blank unless explicitly known

### Step 4 — Validate

Run the QA checklist before writing.

### Step 5 — Append and flush

Append one row immediately.

After each append:

- flush the file handle
- optionally fsync for extra safety
- update `progress.json`

### Step 6 — Failure path

If an image cannot be analyzed confidently:

- write filename and reason to `review_needed.csv`
- continue with next image

## Keyword generation rule of thumb

For this project, use this order:

1. broad subject class
2. visible object/subject
3. subtype/species/content class
4. composition terms
5. lighting / mood / environment
6. supporting but relevant detail

Example for a cat portrait:

```text
animal, pet, feline, domestic cat, portrait, close up, orange cat, black background
```

Do not mechanically pad to 49 keywords. Adobe notes that 15–35 strong keywords are often enough, while accuracy matters more than volume. citeturn472567view2

## Title rule of thumb

Pattern:

`[main subject] + [context] + [key visual detail]`

Example:

```text
Orange and white cat portrait against black background
```

Avoid:

- commas
- poetic copy
- sales copy
- keyword strings
- brand references

## Category decision rule of thumb

Pick the **single best** category, not all possible categories.

Examples:

- bridge across lake = 11 Landscapes if the scenic view dominates
- close-up facade or interior = 2 Buildings and Architecture
- ferry, airplane, traffic signal, road scene = 20 Transport
- leaf macro or dew on plant = 14 Plants and Flowers
- animal portraits and wildlife = 1 Animals
- plated meal or pastry = 7 Food
- beverage-only shot = 4 Drinks

## Recommended outputs

```text
/Users/administrator/Documents/Kiki/adobe_stock_csv_codex_kit/
├── Portfolio/
└── output/
    ├── adobe_stock_upload.csv
    ├── progress.json
    ├── review_needed.csv
    └── run.log
```
