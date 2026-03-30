#!/usr/bin/env python3
"""Resumable Adobe Stock CSV generator with incremental writes."""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HEADER = ["Filename", "Title", "Keywords", "Category", "Releases"]
REVIEW_HEADER = ["Filename", "Reason"]
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OLLAMA_MODEL = "qwen3.5:2b-fast"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_LMSTUDIO_MODEL = "qwen3-vl-8b"
DEFAULT_LMSTUDIO_HOST = "http://127.0.0.1:1234"
DEFAULT_LMSTUDIO_API_KEY = "lm-studio"
MAX_TITLE_LEN = 200


class ValidationError(Exception):
    """Raised when generated metadata is invalid."""


class AnalyzerUnavailableError(Exception):
    """Raised when the analyzer cannot be used for this run."""


class ImageAnalyzer(Protocol):
    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        """Return metadata payload for one image."""


@dataclass
class RowData:
    filename: str
    title: str
    keywords: list[str]
    category: int
    releases: str = ""

    def to_csv_row(self) -> list[str]:
        keywords_cell = ", ".join(self.keywords)
        return [self.filename, self.title, keywords_cell, str(self.category), self.releases]


class OpenAIVisionAnalyzer:
    """Minimal OpenAI Vision API adapter via chat completions."""

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        if not self.api_key:
            raise AnalyzerUnavailableError("OPENAI_API_KEY is not set")
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, mime = _read_image_as_data_url_parts(image_path)
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate Adobe Stock CSV metadata for one image. "
                        "Return JSON only with keys: title (string), keywords (array of strings), "
                        "category (integer 1-21), releases (string). "
                        "Use English only. Title must be factual, no commas, <= 70 chars preferred. "
                        "No brands, logos, trademarks, copyrighted character names, or camera metadata. "
                        "Keywords must be relevant, ordered most important first, max 49, no duplicates. "
                        "Keep releases empty unless explicitly verified. "
                        "Category mapping hints: animals=1, architecture/interiors=2, drinks=4, food=7, "
                        "scenic landscapes=11, plants/flowers=14, religion-dominant worship context=15, "
                        "transport scenes=20, destination/travel context=21."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this image and return only JSON with this exact shape: "
                                '{"title":"...","keywords":["..."],"category":1,"releases":""}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_data}"},
                        },
                    ],
                },
            ],
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnalyzerUnavailableError(f"OpenAI HTTP {exc.code}: {detail[:240]}") from exc
        except URLError as exc:
            raise AnalyzerUnavailableError(f"OpenAI network error: {exc}") from exc

        try:
            outer = json.loads(body)
            content = outer["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            payload_json = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AnalyzerUnavailableError("OpenAI response parsing failed") from exc

        return payload_json


class OllamaVisionAnalyzer:
    """Local Ollama Vision API adapter via /api/chat."""

    def __init__(self, host: str, model: str) -> None:
        self.host = host.rstrip("/")
        self.model = model

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, _ = _read_image_as_data_url_parts(image_path)
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate Adobe Stock CSV metadata for one image. "
                        "Return JSON only with keys: title (string), keywords (array of strings), "
                        "category (integer 1-21), releases (string). "
                        "Use English only. Title must be factual, no commas, <= 70 chars preferred. "
                        "No brands, logos, trademarks, copyrighted character names, or camera metadata. "
                        "Keywords must be relevant, ordered most important first, max 49, no duplicates. "
                        "Keep releases empty unless explicitly verified. "
                        "Category mapping hints: animals=1, architecture/interiors=2, drinks=4, food=7, "
                        "scenic landscapes=11, plants/flowers=14, religion-dominant worship context=15, "
                        "transport scenes=20, destination/travel context=21."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze this image and return only JSON with this exact shape: "
                        '{"title":"...","keywords":["..."],"category":1,"releases":""}'
                    ),
                    "images": [b64_data],
                },
            ],
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            f"{self.host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnalyzerUnavailableError(f"Ollama HTTP {exc.code}: {detail[:240]}") from exc
        except URLError as exc:
            raise AnalyzerUnavailableError(f"Ollama network error: {exc}") from exc

        try:
            outer = json.loads(body)
            message = outer["message"]["content"]
            payload_json = json.loads(message)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AnalyzerUnavailableError("Ollama response parsing failed") from exc

        return payload_json


class LMStudioVisionAnalyzer:
    """LM Studio local server adapter via OpenAI-compatible /v1/chat/completions."""

    def __init__(self, host: str, model: str, api_key: str | None = None) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key or ""

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, mime = _read_image_as_data_url_parts(image_path)
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You generate Adobe Stock CSV metadata for one image. "
                        "Return JSON only with keys: title (string), keywords (array of strings), "
                        "category (integer 1-21), releases (string). "
                        "Use English only. Title must be factual, no commas, <= 70 chars preferred. "
                        "No brands, logos, trademarks, copyrighted character names, or camera metadata. "
                        "Keywords must be relevant, ordered most important first, max 49, no duplicates. "
                        "Keep releases empty unless explicitly verified. "
                        "Category mapping hints: animals=1, architecture/interiors=2, drinks=4, food=7, "
                        "scenic landscapes=11, plants/flowers=14, religion-dominant worship context=15, "
                        "transport scenes=20, destination/travel context=21."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this image and return only JSON with this exact shape: "
                                '{"title":"...","keywords":["..."],"category":1,"releases":""}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_data}"},
                        },
                    ],
                },
            ],
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            f"{self.host}/v1/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(req, timeout=180) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnalyzerUnavailableError(f"LM Studio HTTP {exc.code}: {detail[:240]}") from exc
        except URLError as exc:
            raise AnalyzerUnavailableError(f"LM Studio network error: {exc}") from exc

        try:
            outer = json.loads(body)
            content = outer["choices"][0]["message"]["content"]
            payload_json = _parse_json_object(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AnalyzerUnavailableError("LM Studio response parsing failed") from exc

        return payload_json


def _read_image_as_data_url_parts(path: Path) -> tuple[str, str]:
    import base64

    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        mime = "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return data, mime


def _parse_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise json.JSONDecodeError("content is not json text", str(content), 0)

    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        text = text.replace("\n```", "").strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("no json object found", text, 0)
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("parsed value is not object", text, start)
    return parsed


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class RunLogger:
    def __init__(self, path: Path, verbose: bool) -> None:
        self.path = path
        self.verbose = verbose
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8", newline="\n")

    def log(self, message: str) -> None:
        line = f"{now_iso()} {message}"
        self._fp.write(line + "\n")
        self._fp.flush()
        os.fsync(self._fp.fileno())
        if self.verbose:
            print(line)

    def close(self) -> None:
        self._fp.close()


@dataclass
class RunConfig:
    portfolio_dir: Path
    output_dir: Path
    limit: int | None = None
    model: str = DEFAULT_OPENAI_MODEL
    dry_run: bool = False
    verbose: bool = False
    backend: str = "lmstudio"
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    lmstudio_model: str = DEFAULT_LMSTUDIO_MODEL
    lmstudio_host: str = DEFAULT_LMSTUDIO_HOST
    lmstudio_api_key: str = DEFAULT_LMSTUDIO_API_KEY


@dataclass
class RunStats:
    total_images: int = 0
    analyzed: int = 0
    written: int = 0
    skipped_existing: int = 0
    skipped_collision: int = 0
    failed: int = 0
    started_at: str = ""
    finished_at: str = ""
    last_filename: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "total_images": self.total_images,
            "analyzed": self.analyzed,
            "written": self.written,
            "skipped_existing": self.skipped_existing,
            "skipped_collision": self.skipped_collision,
            "failed": self.failed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_filename": self.last_filename,
        }


def discover_images(portfolio_dir: Path) -> list[Path]:
    images: list[Path] = []
    for path in portfolio_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        images.append(path)
    images.sort(
        key=lambda p: (
            str(p.relative_to(portfolio_dir)).lower(),
            str(p.relative_to(portfolio_dir)),
        )
    )
    return images


def ensure_csv_header(path: Path, expected_header: list[str]) -> set[str]:
    """Ensure CSV exists with exact header; return processed filename set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    processed: set[str] = set()

    if not path.exists() or path.stat().st_size == 0:
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(
                fp,
                delimiter=",",
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            writer.writerow(expected_header)
            fp.flush()
            os.fsync(fp.fileno())
        return processed

    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp, delimiter=",")
        first = next(reader, None)
        if first != expected_header:
            raise ValueError(
                f"Invalid header in {path}: expected {expected_header}, got {first}"
            )
        for idx, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != len(expected_header):
                raise ValueError(f"Invalid row length at line {idx} in {path}: {len(row)}")
            filename = row[0].strip()
            if filename:
                processed.add(filename.lower())
    return processed


def ensure_review_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(
            fp,
            delimiter=",",
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writerow(REVIEW_HEADER)
        fp.flush()
        os.fsync(fp.fileno())


def normalize_keyword(value: str) -> str:
    return " ".join(value.strip().lower().split())


def validate_row(metadata: dict[str, Any], image_path: Path) -> RowData:
    filename = image_path.name.strip()
    if not filename:
        raise ValidationError("filename is empty")
    if filename != image_path.name:
        raise ValidationError("filename does not match source file")
    if not image_path.exists():
        raise ValidationError("source file does not exist")

    raw_title = metadata.get("title", "")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        raise ValidationError("title is empty")
    if "," in title:
        raise ValidationError("title contains comma")
    if len(title) > MAX_TITLE_LEN:
        raise ValidationError(f"title length exceeds {MAX_TITLE_LEN}")

    raw_keywords = metadata.get("keywords", [])
    if isinstance(raw_keywords, str):
        keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    elif isinstance(raw_keywords, list):
        keywords = [str(k).strip() for k in raw_keywords if str(k).strip()]
    else:
        keywords = []

    if not keywords:
        raise ValidationError("keywords are empty")
    if len(keywords) > 49:
        raise ValidationError("keywords exceed 49")

    normalized = [normalize_keyword(k) for k in keywords]
    if len(set(normalized)) != len(normalized):
        raise ValidationError("keywords contain duplicates after normalization")

    raw_category = metadata.get("category")
    try:
        category = int(raw_category)
    except (TypeError, ValueError) as exc:
        raise ValidationError("category is not an integer") from exc
    if not 1 <= category <= 21:
        raise ValidationError("category out of range 1..21")

    raw_releases = metadata.get("releases", "")
    releases = raw_releases.strip() if isinstance(raw_releases, str) else ""
    # This workflow keeps release blank unless externally verified.
    if releases:
        raise ValidationError("releases must be blank unless verified")

    return RowData(
        filename=filename,
        title=title,
        keywords=keywords,
        category=category,
        releases="",
    )


def append_review(review_writer: csv.writer, review_fp: Any, filename: str, reason: str) -> None:
    review_writer.writerow([filename, reason])
    review_fp.flush()
    os.fsync(review_fp.fileno())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
    ) as tf:
        json.dump(payload, tf, ensure_ascii=False, indent=2)
        tf.write("\n")
        tf.flush()
        os.fsync(tf.fileno())
        tmp_path = Path(tf.name)
    os.replace(tmp_path, path)


def update_progress(path: Path, stats: RunStats, pending: int) -> None:
    payload = stats.to_json()
    payload["pending"] = pending
    atomic_write_json(path, payload)


def quick_validate_csv_structure(csv_path: Path, preview_lines: int = 5) -> tuple[bool, list[str]]:
    if not csv_path.exists():
        return False, [f"CSV not found: {csv_path}"]

    messages: list[str] = []

    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp, delimiter=",")
        rows = list(reader)

    if not rows:
        return False, ["CSV is empty"]

    if rows[0] != HEADER:
        return False, [f"Header mismatch. Expected: {HEADER} Got: {rows[0]}"]

    for i, row in enumerate(rows[1:], start=2):
        if len(row) != 5:
            return False, [f"Row {i} has {len(row)} fields (expected 5)"]

    with csv_path.open("r", encoding="utf-8", newline="") as fp:
        for idx, line in enumerate(fp):
            if idx >= preview_lines:
                break
            messages.append(line.rstrip("\n"))

    return True, messages


def build_analyzer(config: RunConfig) -> ImageAnalyzer:
    backend = config.backend.strip().lower()
    if backend == "lmstudio":
        return LMStudioVisionAnalyzer(
            host=config.lmstudio_host,
            model=config.lmstudio_model,
            api_key=config.lmstudio_api_key,
        )
    if backend == "ollama":
        return OllamaVisionAnalyzer(
            host=config.ollama_host,
            model=config.ollama_model,
        )
    if backend == "openai":
        return OpenAIVisionAnalyzer(
            api_key=os.getenv("OPENAI_API_KEY"),
            model=config.model,
        )
    raise ValueError(f"Unsupported backend: {config.backend}")


def run_batch(config: RunConfig, analyzer: ImageAnalyzer | None = None) -> RunStats:
    portfolio_dir = config.portfolio_dir
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "adobe_stock_upload.csv"
    review_path = output_dir / "review_needed.csv"
    progress_path = output_dir / "progress.json"
    log_path = output_dir / "run.log"

    logger = RunLogger(log_path, verbose=config.verbose)
    stats = RunStats(started_at=now_iso())

    try:
        if not portfolio_dir.exists() or not portfolio_dir.is_dir():
            raise ValueError(f"Portfolio directory not found: {portfolio_dir}")

        image_paths = discover_images(portfolio_dir)
        stats.total_images = len(image_paths)
        processed = ensure_csv_header(csv_path, HEADER)
        ensure_review_csv(review_path)

        if analyzer is None:
            analyzer = build_analyzer(config)

        processed_before_run = set(processed)
        logger.log(f"start total_images={stats.total_images} processed_existing={len(processed)}")
        update_progress(progress_path, stats, pending=max(0, stats.total_images - len(processed)))

        seen_new: dict[str, Path] = {}

        with csv_path.open("a", encoding="utf-8", newline="") as csv_fp, review_path.open(
            "a", encoding="utf-8", newline=""
        ) as review_fp:
            writer = csv.writer(
                csv_fp,
                delimiter=",",
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )
            review_writer = csv.writer(
                review_fp,
                delimiter=",",
                quoting=csv.QUOTE_MINIMAL,
                lineterminator="\n",
            )

            for image_path in image_paths:
                filename = image_path.name
                key = filename.lower()

                existing_path = seen_new.get(key)
                if (
                    existing_path
                    and existing_path != image_path
                    and key not in processed_before_run
                ):
                    reason = f"duplicate basename collision with {existing_path.relative_to(portfolio_dir)}"
                    append_review(review_writer, review_fp, filename, reason)
                    stats.skipped_collision += 1
                    stats.failed += 1
                    logger.log(f"review filename={filename} reason={reason}")
                    stats.last_filename = filename
                    update_progress(
                        progress_path,
                        stats,
                        pending=max(0, stats.total_images - len(processed) - stats.failed),
                    )
                    continue

                if key in processed:
                    stats.skipped_existing += 1
                    logger.log(f"skip existing filename={filename}")
                    continue

                seen_new[key] = image_path

                if config.limit is not None and stats.analyzed >= config.limit:
                    logger.log(f"stop limit reached limit={config.limit}")
                    break

                try:
                    metadata = analyzer.analyze_image(image_path)
                    row = validate_row(metadata, image_path)
                except (AnalyzerUnavailableError, ValidationError, TimeoutError, OSError) as exc:
                    reason = str(exc)
                    append_review(review_writer, review_fp, filename, reason)
                    stats.failed += 1
                    stats.analyzed += 1
                    stats.last_filename = filename
                    logger.log(f"review filename={filename} reason={reason}")
                    update_progress(
                        progress_path,
                        stats,
                        pending=max(0, stats.total_images - len(processed) - stats.failed),
                    )
                    continue

                if config.dry_run:
                    logger.log(f"dry-run valid filename={filename}")
                else:
                    writer.writerow(row.to_csv_row())
                    csv_fp.flush()
                    os.fsync(csv_fp.fileno())
                    processed.add(key)
                    stats.written += 1
                    logger.log(
                        f"write filename={filename} category={row.category} keywords={len(row.keywords)}"
                    )

                stats.analyzed += 1
                stats.last_filename = filename
                update_progress(
                    progress_path,
                    stats,
                    pending=max(0, stats.total_images - len(processed) - stats.failed),
                )

        stats.finished_at = now_iso()
        logger.log(
            "done "
            f"written={stats.written} failed={stats.failed} "
            f"skipped_existing={stats.skipped_existing} skipped_collision={stats.skipped_collision}"
        )
        update_progress(progress_path, stats, pending=max(0, stats.total_images - len(processed) - stats.failed))
        return stats
    finally:
        logger.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Adobe Stock CSV metadata incrementally")
    parser.add_argument(
        "--portfolio-dir",
        type=Path,
        default=Path("Portfolio"),
        help="Portfolio directory to scan recursively",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory where CSV/log/progress files are written",
    )
    parser.add_argument("--limit", type=int, default=None, help="Analyze at most N images in this run")
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        help="OpenAI model for --backend openai",
    )
    parser.add_argument(
        "--backend",
        choices=["lmstudio", "ollama", "openai"],
        default="lmstudio",
        help="Metadata analyzer backend (default: lmstudio)",
    )
    parser.add_argument(
        "--lmstudio-model",
        type=str,
        default=os.getenv("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL),
        help="LM Studio vision model for --backend lmstudio",
    )
    parser.add_argument(
        "--lmstudio-host",
        type=str,
        default=os.getenv("LMSTUDIO_HOST", DEFAULT_LMSTUDIO_HOST),
        help="LM Studio host URL for --backend lmstudio",
    )
    parser.add_argument(
        "--lmstudio-api-key",
        type=str,
        default=os.getenv("LMSTUDIO_API_KEY", DEFAULT_LMSTUDIO_API_KEY),
        help="LM Studio API key (optional; default works for local server)",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        default=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        help="Ollama vision model for --backend ollama",
    )
    parser.add_argument(
        "--ollama-host",
        type=str,
        default=os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help="Ollama host URL for --backend ollama",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze/validate without appending upload CSV rows",
    )
    parser.add_argument("--verbose", action="store_true", help="Print run logs to stdout")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate output/adobe_stock_upload.csv structure and print first lines",
    )
    parser.add_argument(
        "--validate-lines",
        type=int,
        default=5,
        help="Number of first lines to print during --validate-only",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.validate_only:
        csv_path = args.output_dir / "adobe_stock_upload.csv"
        ok, messages = quick_validate_csv_structure(csv_path, preview_lines=args.validate_lines)
        if ok:
            print("CSV structure check: PASS")
            print("First lines:")
            for line in messages:
                print(line)
            return 0
        print("CSV structure check: FAIL")
        for msg in messages:
            print(msg)
        return 1

    try:
        stats = run_batch(
            RunConfig(
                portfolio_dir=args.portfolio_dir,
                output_dir=args.output_dir,
                limit=args.limit,
                model=args.model,
                dry_run=args.dry_run,
                verbose=args.verbose,
                backend=args.backend,
                ollama_model=args.ollama_model,
                ollama_host=args.ollama_host,
                lmstudio_model=args.lmstudio_model,
                lmstudio_host=args.lmstudio_host,
                lmstudio_api_key=args.lmstudio_api_key,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        "Run finished: "
        f"written={stats.written} failed={stats.failed} analyzed={stats.analyzed} "
        f"skipped_existing={stats.skipped_existing} skipped_collision={stats.skipped_collision}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
