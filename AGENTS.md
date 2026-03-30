# AGENTS.md

You are implementing a **local Adobe Stock CSV metadata generator** for image files stored at:

`/Users/administrator/Documents/Kiki/adobe_stock_csv_codex_kit/Portfolio`

Read these files before changing code:

1. `README.md`
2. `docs/CSV_SPEC.md`
3. `docs/WORKFLOW.md`
4. `docs/QA_CHECKLIST.md`

## Primary goal

Analyze each image file and write one Adobe Stock CSV row for it using this exact column order:

`Filename,Title,Keywords,Category,Releases`

## Non-negotiable rules

1. **Process images one by one**.
2. **Append one CSV row immediately after each successful analysis**.
3. **Flush data to disk after every appended row**.
4. **Support resume**: if a filename already exists in the output CSV, skip it.
5. **Do not rewrite the whole CSV on each iteration**.
6. **Do not use Numbers, Excel, pandas spreadsheet export defaults, or any spreadsheet app as the final writer**.
7. Use a plain text CSV writer with:
   - UTF-8 encoding
   - comma delimiter
   - standard CSV quoting
   - exact header names
   - LF newline (`\n`)
8. Leave `Releases` empty unless a verified release name is provided.
9. Do not invent unreleased model or property release names.
10. Do not include brand names, logos, copyrighted character names, trademarks, camera specs, or irrelevant concepts in titles or keywords.
11. Keep all metadata in **English only** for this workflow.
12. Keep titles factual, short, and without commas.
13. Keep keywords ordered from most important to least important.
14. Use **49 keywords max**.
15. Use numeric category values only.

## CSV constraints

- Header row must be exactly:
  `Filename,Title,Keywords,Category,Releases`
- Each keyword field is a comma-separated list inside one CSV cell.
- Category must be one integer from 1 to 21.
- Filename must include extension.

## Title rules

- Short factual phrase
- Prefer <= 70 characters
- Hard stop at 200 characters if needed by local template compatibility
- No commas
- No keyword stuffing

## Keyword rules

- Most important first
- Prefer 15–35 strong keywords unless the image clearly supports more
- Absolute maximum: 49
- No duplicates
- No plural/singular spam
- No full-sentence phrases unless it is a true fixed concept
- Do not add speculative commercial use cases that are not visible in the image

## Category mapping

Use the Adobe numeric categories exactly:

1 Animals
2 Buildings and Architecture
3 Business
4 Drinks
5 The Environment
6 States of Mind
7 Food
8 Graphic Resources
9 Hobbies and Leisure
10 Industry
11 Landscapes
12 Lifestyle
13 People
14 Plants and Flowers
15 Culture and Religion
16 Science
17 Social Issues
18 Sports
19 Technology
20 Transport
21 Travel

## Preferred classification guidance for this photo library

- animals, birds, insects, pets -> 1
- architecture, interiors, skylines, facades -> 2
- beverage-as-main-subject -> 4
- food-as-main-subject -> 7
- pure nature vista / sky / waterfall / scenic land or city view -> 11
- plant macro / leaf detail / flowers -> 14
- mosque/interior where religion is dominant, not just architecture -> 15
- roads, cars, planes, ferries, traffic signals -> 20
- destination/trip context dominant over pure scenery -> 21

When an image could fit both 2 and 15, prefer:
- **2** if the image is mainly about structure, form, interior, facade, or geometry
- **15** only if faith/religion/cultural worship context is clearly the main subject

## Implementation requirements

Create a tool that:

- recursively scans the target folder for image files
- filters to supported image extensions
- sorts files deterministically
- loads existing CSV rows
- skips already processed filenames
- analyzes one image at a time
- validates the generated metadata
- appends a single valid row
- writes failures to `output/review_needed.csv`
- logs progress to `output/run.log`

## Validation requirements

Before writing a row, validate:

- filename not empty
- title not empty
- no comma in title
- title length within configured limit
- keyword count between 1 and 49
- no duplicate keywords after normalization
- category in 1..21
- release blank unless explicitly provided

## Failure handling

If one image fails analysis or validation:

- do not stop the whole batch
- record the filename and reason in `review_needed.csv`
- continue with the next image

## Deliverable expectation

The implementation should be a simple local CLI tool that can be rerun safely until the folder is fully processed.
