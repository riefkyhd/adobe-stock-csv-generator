# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog.

## [Unreleased]

## [0.1.0] - 2026-03-31

### Added

- Resumable Adobe Stock CSV CLI with one-row incremental write and flush safety.
- Local analyzer backend support (LM Studio and Ollama) plus optional OpenAI backend.
- CSV validation, review queue output, progress tracking, and run logging.
- Benchmark metrics (avg/p50/p95/max, throughput, warmup timing).
- Tests covering resume, validation, collision handling, fallback paths, and output structure checks.

### Changed

- Improved English-only metadata cleanup and location-generalized keyword policy.
- Hardened LM Studio workflow with fallback handling and category resolver support.
