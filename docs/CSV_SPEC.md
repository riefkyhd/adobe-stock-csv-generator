# CSV_SPEC.md

## Exact CSV format required for this project

Use this header row exactly:

```csv
Filename,Title,Keywords,Category,Releases
```

## File writing rules

- Encoding: **UTF-8**
- Delimiter: **comma**
- Newlines: **LF (`\n`)**
- Quote fields only when needed, using standard CSV rules
- Do not add extra columns
- Do not translate header names
- Do not prepend comment rows
- Do not save as XLSX, TSV, Numbers, or semicolon-delimited CSV

## Why this matters

Adobe requires exact header names, exact filenames, and CSV UTF-8 handling for spreadsheet-generated files. Adobe’s own sample CSV is plain text CSV with the header above and comma-separated fields. Adobe also says the filename, title, and keywords columns are required, and category is strongly recommended. citeturn472567view0turn761901view3turn472567view1

## Column rules

### 1) Filename

- Must match the uploaded asset filename exactly
- Must include file extension
- Preflight warning if filename is longer than 30 characters

Example:

```text
IMG_1024.JPG
```

### 2) Title

Use a short factual description.

Rules:

- Prefer **70 characters or fewer**
- No commas
- No keyword list style
- No brands, logos, character names, or trademarks
- English only for this workflow

Good example:

```text
Orange and white cat portrait against black background
```

Bad example:

```text
cat, orange, cute, pet, whiskers, fluffy
```

Adobe explicitly recommends titles that are short, factual, accurate, and limited to about 70 characters. citeturn761901view0turn472567view1

### 3) Keywords

Rules:

- Comma-separated inside one CSV cell
- Order by importance
- Target 20–30 strong keywords when justified by visible content
- Minimum accepted keyword count: 15 after cleanup
- Max **49** keywords for this workflow
- Duplicates are automatically removed after normalization (trim/lower/space-fold)
- Major title concepts should be present in top keywords when relevant
- Remove technical/file metadata and trademark/IP terms
- No technical metadata
- No keyword stuffing
- Avoid unnecessary singular/plural variants
- Use one language consistently

Preferred style:

```text
animal, pet, feline, domestic cat, portrait, orange cat, black background
```

Adobe says keyword order is critical, the first 10 matter most, and up to 49 keywords are supported. It also warns against keyword spamming and recommends keeping metadata accurate and in a single language. citeturn472567view1turn472567view2

### 4) Category

Write the category number only.

```text
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
```

Adobe uses numeric categories in CSV uploads. citeturn472567view0turn963758search0

### 5) Releases

- Leave blank for this project by default
- Only populate if an actual release file has already been uploaded to Adobe Stock and the exact release filename is known

## Important compatibility note

Even if Numbers or Excel produce a file that looks correct visually, Adobe may reject or fail to parse it if delimiter, encoding, quoting, or header formatting differs from the required structure. The safest path is to generate the CSV directly with a script that writes plain UTF-8 text. Adobe explicitly calls out exact headers and CSV UTF-8 handling for Excel users outside the US. citeturn472567view0

## Recommended local writer behavior

Use a CSV writer configured like this conceptually:

- `encoding='utf-8'`
- `newline=''`
- `lineterminator='\n'`
- standard CSV quoting

## Recommended output example

```csv
Filename,Title,Keywords,Category,Releases
cat_001.jpg,Orange and white cat portrait against black background,"animal, pet, feline, domestic cat, portrait, orange cat, black background",1,
bridge_001.jpg,Suspension bridge reflected in calm lake,"architecture, landscape, bridge, reflection, lake, trees, calm water",11,
```
