# CODEX_PROMPT.md

Use this prompt in Codex:

---

You are implementing a **local Adobe Stock CSV metadata generator** for my image library.

Repository/workspace goal:
Build a simple, resumable local tool that scans image files from:

`/Users/administrator/Documents/Kiki/adobe_stock_csv_codex_kit/Portfolio`

and generates a valid Adobe Stock upload CSV.

Before doing anything:
1. Read `AGENTS.md`
2. Read `README.md`
3. Read `docs/CSV_SPEC.md`
4. Read `docs/WORKFLOW.md`
5. Read `docs/QA_CHECKLIST.md`

## What I want

Create a small local CLI tool that:

- scans the target folder recursively for image files
- analyzes images one by one
- generates Adobe Stock metadata for each image
- appends exactly one validated CSV row per successfully analyzed image
- flushes progress immediately so the run can resume after interruption
- skips filenames already present in the output CSV
- logs failures without stopping the full batch

## Important workflow decision

Do **not** analyze all files first and write once at the end.
Use this safer strategy:

- analyze 1 image
- validate metadata
- append 1 row
- flush
- continue

## CSV output requirements

Output path:

`/Users/administrator/Documents/Kiki/adobe_stock_csv_codex_kit/output/adobe_stock_upload.csv`

Header must be exactly:

`Filename,Title,Keywords,Category,Releases`

Rules:

- write plain UTF-8 CSV directly from code
- use comma delimiter
- use standard CSV quoting
- use LF newlines
- do not use Numbers or Excel as the final writer
- title required
- keywords required
- filename required
- category numeric only
- releases blank by default

## Metadata rules

### Title
- short factual phrase
- no commas
- English only
- ideally <= 70 chars
- never keyword-stuffed
- no brands, no logos, no trademarks, no character names

### Keywords
- max 49
- most important first
- general to specific
- no duplicates
- no technical metadata
- no keyword spam
- top 10 should carry the strongest search terms

### Category
Use Adobe numeric categories 1–21 exactly as documented in `AGENTS.md`.

## Category guidance for this photo library

Use these defaults unless the image clearly indicates otherwise:

- animals, birds, insects, pets -> 1
- architecture, interiors, skylines, facades -> 2
- drink-only subject -> 4
- food-only subject -> 7
- scenic nature / sky / waterfall / vista -> 11
- leaf / plant / flower macro -> 14
- religion-dominant interior -> 15
- vehicles / roads / traffic / planes / ferries -> 20
- trip/destination context dominates -> 21

## Required outputs

Create:

- `output/adobe_stock_upload.csv`
- `output/review_needed.csv`
- `output/progress.json`
- `output/run.log`

## Validation

Before writing any row, validate:

- filename exists and matches actual file
- title not empty
- title contains no comma
- keyword count <= 49
- no duplicate keywords after normalization
- category is integer 1..21
- releases blank unless exact release filename is known

## Failure behavior

If an image cannot be analyzed confidently:

- do not stop the run
- append a note to `review_needed.csv`
- continue processing the next image

## Implementation style

- keep it simple
- Python is preferred
- explain affected files before major edits
- favor robustness over cleverness
- make the tool resumable and deterministic
- sort files before processing
- support reruns safely

## Final deliverables

I want:
1. the implemented tool
2. a short README section explaining how to run it
3. a quick validation step that compares the first lines of the produced CSV to the Adobe sample structure

If there is any conflict between a looser sample/template note and Adobe’s current Contributor help guidance, follow the stricter rule that is safer for acceptance.

---
