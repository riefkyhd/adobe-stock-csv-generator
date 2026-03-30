# QA_CHECKLIST.md

Use this checklist before accepting a generated row.

## Per-row validation

### Filename

- [ ] exact filename with extension
- [ ] matches local file exactly
- [ ] warning if > 30 chars

### Title

- [ ] not empty
- [ ] no comma
- [ ] short and factual
- [ ] English only
- [ ] ideally <= 70 chars
- [ ] no brands, no logos, no trademarks, no fictional character names
- [ ] no keyword stuffing

### Keywords

- [ ] not empty
- [ ] <= 49 keywords
- [ ] ordered most important first
- [ ] no duplicates
- [ ] no irrelevant words
- [ ] no technical metadata
- [ ] no spammy plural/singular padding
- [ ] same language as title
- [ ] top 10 contain the strongest search terms

### Category

- [ ] integer only
- [ ] value between 1 and 21
- [ ] best single-fit category selected

### Releases

- [ ] blank unless exact uploaded release filename is known

## File-level validation

- [ ] header row exactly matches Adobe sample
- [ ] comma-delimited CSV
- [ ] UTF-8 encoding
- [ ] opens as plain text correctly
- [ ] each data row has exactly 5 fields
- [ ] keyword cells remain quoted correctly when commas are present
- [ ] no extra blank columns
- [ ] no translated header names

## Upload sanity checks

Before uploading to Adobe Stock:

- [ ] compare first few lines with Adobe sample structure
- [ ] verify filenames match uploaded assets exactly
- [ ] confirm category numbers are numeric, not words
- [ ] confirm no accidental spreadsheet corruption
- [ ] confirm batch size <= 5000 rows and file size <= 1MB if needed

Adobe’s CSV help page requires exact headers, required fields, UTF-8 handling, and numeric categories, and sets limits of 5,000 rows and 1MB overall file size. citeturn472567view0
