#!/usr/bin/env python3
"""Resumable Adobe Stock CSV generator with incremental writes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import mimetypes
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
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
DEFAULT_LMSTUDIO_FALLBACK_MODEL = "google/gemma-3-4b"
DEFAULT_LMSTUDIO_CATEGORY_MODEL = "google/gemma-3-4b"
DEFAULT_LMSTUDIO_HOST = "http://127.0.0.1:1234"
DEFAULT_LMSTUDIO_API_KEY = "lm-studio"
DEFAULT_LMSTUDIO_TIMEOUT_SECONDS = 180
DEFAULT_LMSTUDIO_MAX_TOKENS = 450
DEFAULT_LMSTUDIO_TOP_P = 0.9
DEFAULT_LMSTUDIO_TOP_K = 40
MIN_KEYWORDS = 15
TARGET_KEYWORD_LOW = 20
TARGET_KEYWORD_HIGH = 30
MAX_KEYWORDS = 49
MAX_TITLE_LEN = 200

TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

GENERIC_LOW_VALUE_KEYWORDS = {
    "photo",
    "photograph",
    "photography",
    "image",
    "picture",
    "stock photo",
    "stock image",
    "wallpaper",
}

BLOCKED_IP_KEYWORDS = {
    "adobe",
    "airbnb",
    "android",
    "canon",
    "coca cola",
    "facebook",
    "gopro",
    "google",
    "instagram",
    "ipad",
    "iphone",
    "lego",
    "mercedes",
    "microsoft",
    "netflix",
    "nikon",
    "nintendo",
    "porsche",
    "samsung",
    "sony",
    "tesla",
    "tiktok",
    "twitter",
    "youtube",
}

# Keep metadata generally marketable and avoid unverifiable geo-specific claims.
LOCATION_PHRASES = {
    "abu dhabi",
    "bali indonesia",
    "bangkok thailand",
    "hong kong",
    "kuala lumpur",
    "los angeles",
    "new york",
    "new york city",
    "san francisco",
    "saudi arabia",
    "singapore city",
    "united arab emirates",
    "united kingdom",
    "united states",
    "west java",
}

LOCATION_TERMS = {
    "africa",
    "algeria",
    "america",
    "argentina",
    "asia",
    "australia",
    "austria",
    "bali",
    "bangkok",
    "belgium",
    "brazil",
    "canada",
    "china",
    "colombia",
    "denmark",
    "dubai",
    "egypt",
    "england",
    "europe",
    "france",
    "germany",
    "greece",
    "india",
    "indonesia",
    "iran",
    "iraq",
    "ireland",
    "italy",
    "jakarta",
    "japan",
    "jeddah",
    "jordan",
    "korea",
    "kuwait",
    "london",
    "malaysia",
    "mexico",
    "morocco",
    "netherlands",
    "norway",
    "pakistan",
    "paris",
    "philippines",
    "qatar",
    "russia",
    "saudi",
    "singapore",
    "spain",
    "sweden",
    "switzerland",
    "thailand",
    "tokyo",
    "turkey",
    "uae",
    "uk",
    "usa",
    "vietnam",
}

NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")


class ValidationError(Exception):
    """Raised when generated metadata is invalid."""


class AnalyzerUnavailableError(Exception):
    """Raised when the analyzer cannot be used for this run."""


class ImageAnalyzer(Protocol):
    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        """Return metadata payload for one image."""


class CategoryResolver(Protocol):
    def resolve_category(
        self,
        image_path: Path,
        title: str,
        keywords: list[str],
        proposed_category: int | None = None,
    ) -> int:
        """Return one Adobe numeric category (1..21)."""


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
                        "Avoid specific city, country, or landmark names unless externally verified; use general location terms. "
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
                        "Avoid specific city, country, or landmark names unless externally verified; use general location terms. "
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

    def __init__(
        self,
        host: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: int = DEFAULT_LMSTUDIO_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_LMSTUDIO_MAX_TOKENS,
        top_p: float = DEFAULT_LMSTUDIO_TOP_P,
        top_k: int = DEFAULT_LMSTUDIO_TOP_K,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.top_k = top_k

    def _post_chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "messages": messages,
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
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnalyzerUnavailableError(f"LM Studio HTTP {exc.code}: {detail[:240]}") from exc
        except URLError as exc:
            raise AnalyzerUnavailableError(f"LM Studio network error: {exc}") from exc
        except TimeoutError as exc:
            raise AnalyzerUnavailableError(
                f"LM Studio timeout after {self.timeout_seconds}s"
            ) from exc

        try:
            outer = json.loads(body)
            content = outer["choices"][0]["message"]["content"]
            return _parse_json_object(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AnalyzerUnavailableError("LM Studio response parsing failed") from exc

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, mime = _read_image_as_data_url_parts(image_path)
        return self._post_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You generate Adobe Stock CSV metadata for one image. "
                        "Return JSON only with keys: title (string), keywords (array of strings), "
                        "category (integer 1-21), releases (string). "
                        "Use English only. Title must be factual, no commas, <= 70 chars preferred. "
                        "No brands, logos, trademarks, copyrighted character names, or camera metadata. "
                        "Avoid specific city, country, or landmark names unless externally verified; use general location terms. "
                        "Keywords must be relevant, buyer-focused, and ordered by importance from strongest first. "
                        f"Target {TARGET_KEYWORD_LOW}-{TARGET_KEYWORD_HIGH} keywords when justified by visible content; "
                        f"hard max {MAX_KEYWORDS}. Avoid keyword spam and duplicates. "
                        "Cover who/what, action, setting, visual details, mood/concept, viewpoint, and location when clearly visible. "
                        "Put critical search terms in top 10 and include title concepts among strong early terms. "
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
            ]
        )

    def enrich_keywords(
        self,
        image_path: Path,
        title: str,
        existing_keywords: list[str],
        category: int | None,
    ) -> list[str]:
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, mime = _read_image_as_data_url_parts(image_path)
        category_text = str(category) if isinstance(category, int) else "unknown"
        response = self._post_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You improve Adobe Stock keyword relevance for one image. "
                        "Return JSON only with key 'keywords' (array of strings). "
                        "Generate only missing, relevant keywords that are not duplicates of existing terms. "
                        "Do not return title, category, or releases. "
                        f"Target final keyword list {TARGET_KEYWORD_LOW}-{TARGET_KEYWORD_HIGH}; hard max {MAX_KEYWORDS}. "
                        "No brands, logos, trademarks, copyrighted character names, camera/exif/file metadata, or spam. "
                        "Avoid specific city, country, or landmark names unless externally verified; keep terms general."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Given this image, propose only additional missing keywords.\n"
                                f"Title: {title}\n"
                                f"Category: {category_text}\n"
                                f"Existing keywords ({len(existing_keywords)}): {', '.join(existing_keywords)}\n"
                                "Return only JSON in this shape: {\"keywords\":[\"...\"]}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_data}"},
                        },
                    ],
                },
            ]
        )

        raw = response.get("keywords", [])
        if isinstance(raw, str):
            return [k.strip() for k in raw.split(",") if k.strip()]
        if isinstance(raw, list):
            return [str(k).strip() for k in raw if str(k).strip()]
        return []

    def resolve_category(
        self,
        image_path: Path,
        title: str,
        keywords: list[str],
        proposed_category: int | None = None,
    ) -> int:
        if not image_path.exists() or not image_path.is_file():
            raise AnalyzerUnavailableError("image file not found")

        b64_data, mime = _read_image_as_data_url_parts(image_path)
        proposed_text = str(proposed_category) if isinstance(proposed_category, int) else "unknown"
        response = self._post_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You select one Adobe Stock category for one image. "
                        "Return JSON only with key 'category' as integer 1..21. "
                        "Use this mapping exactly: "
                        "1 Animals, 2 Buildings and Architecture, 3 Business, 4 Drinks, 5 The Environment, "
                        "6 States of Mind, 7 Food, 8 Graphic Resources, 9 Hobbies and Leisure, 10 Industry, "
                        "11 Landscapes, 12 Lifestyle, 13 People, 14 Plants and Flowers, 15 Culture and Religion, "
                        "16 Science, 17 Social Issues, 18 Sports, 19 Technology, 20 Transport, 21 Travel. "
                        "For this portfolio: animals->1, architecture->2, drink->4, food->7, scenic landscapes->11, "
                        "plants/flowers->14, religion-dominant->15, transport->20, destination/travel->21."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Choose the single best category number.\n"
                                f"Title: {title}\n"
                                f"Keywords: {', '.join(keywords[:20])}\n"
                                f"Proposed category: {proposed_text}\n"
                                "Return only JSON in this shape: {\"category\":14}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64_data}"},
                        },
                    ],
                },
            ]
        )

        try:
            category = int(response.get("category"))
        except (TypeError, ValueError) as exc:
            raise AnalyzerUnavailableError("category resolver parsing failed") from exc
        if not 1 <= category <= 21:
            raise AnalyzerUnavailableError("category resolver out-of-range value")
        return category


class LMStudioFallbackAnalyzer:
    """Try a primary LM Studio model, then optional fallback model."""

    def __init__(
        self,
        primary: LMStudioVisionAnalyzer,
        fallback: LMStudioVisionAnalyzer | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._last_successful: LMStudioVisionAnalyzer = primary

    def _with_fallback(self, fn_name: str, *args: Any) -> Any:
        primary_fn = getattr(self.primary, fn_name)
        try:
            result = primary_fn(*args)
            self._last_successful = self.primary
            return result
        except AnalyzerUnavailableError as primary_exc:
            if self.fallback is None:
                raise
            fallback_fn = getattr(self.fallback, fn_name)
            try:
                result = fallback_fn(*args)
                self._last_successful = self.fallback
                return result
            except AnalyzerUnavailableError as fallback_exc:
                raise AnalyzerUnavailableError(
                    f"primary model '{self.primary.model}' failed: {primary_exc}; "
                    f"fallback model '{self.fallback.model}' failed: {fallback_exc}"
                ) from fallback_exc

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        return self._with_fallback("analyze_image", image_path)

    def enrich_keywords(
        self,
        image_path: Path,
        title: str,
        existing_keywords: list[str],
        category: int | None,
    ) -> list[str]:
        preferred = self._last_successful
        try:
            return preferred.enrich_keywords(image_path, title, existing_keywords, category)
        except AnalyzerUnavailableError:
            return self._with_fallback(
                "enrich_keywords",
                image_path,
                title,
                existing_keywords,
                category,
            )


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


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[int(index)]
    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    return lower_value + (upper_value - lower_value) * (index - lower)


@dataclass
class BenchmarkTracker:
    """Tracks per-image timings for model and end-to-end benchmarking."""

    analysis_samples: list[tuple[str, float]] = field(default_factory=list)
    total_samples: list[tuple[str, float]] = field(default_factory=list)

    def add(self, filename: str, analysis_seconds: float, total_seconds: float) -> None:
        self.analysis_samples.append((filename, analysis_seconds))
        self.total_samples.append((filename, total_seconds))

    def summary(self) -> dict[str, Any]:
        analysis_values = [value for _, value in self.analysis_samples]
        total_values = [value for _, value in self.total_samples]
        count = len(total_values)

        slowest_analysis_file = ""
        slowest_analysis_seconds = 0.0
        if self.analysis_samples:
            slowest_analysis_file, slowest_analysis_seconds = max(
                self.analysis_samples, key=lambda item: item[1]
            )

        slowest_total_file = ""
        slowest_total_seconds = 0.0
        if self.total_samples:
            slowest_total_file, slowest_total_seconds = max(self.total_samples, key=lambda item: item[1])

        total_elapsed = sum(total_values)
        throughput_images_per_min = (count / (total_elapsed / 60.0)) if total_elapsed > 0 else 0.0
        warmup_seconds = total_values[0] if total_values else 0.0

        return {
            "image_count": count,
            "analysis_avg_seconds": (sum(analysis_values) / len(analysis_values))
            if analysis_values
            else 0.0,
            "analysis_p50_seconds": _percentile(analysis_values, 0.50),
            "analysis_p95_seconds": _percentile(analysis_values, 0.95),
            "analysis_max_seconds": slowest_analysis_seconds,
            "analysis_slowest_filename": slowest_analysis_file,
            "total_avg_seconds": (sum(total_values) / len(total_values)) if total_values else 0.0,
            "total_p50_seconds": _percentile(total_values, 0.50),
            "total_p95_seconds": _percentile(total_values, 0.95),
            "total_max_seconds": slowest_total_seconds,
            "total_slowest_filename": slowest_total_file,
            "throughput_images_per_min": throughput_images_per_min,
            "warmup_first_image_seconds": warmup_seconds,
        }


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
    lmstudio_fallback_model: str = DEFAULT_LMSTUDIO_FALLBACK_MODEL
    lmstudio_category_model: str = DEFAULT_LMSTUDIO_CATEGORY_MODEL
    lmstudio_host: str = DEFAULT_LMSTUDIO_HOST
    lmstudio_api_key: str = DEFAULT_LMSTUDIO_API_KEY
    lmstudio_timeout_seconds: int = DEFAULT_LMSTUDIO_TIMEOUT_SECONDS
    lmstudio_max_tokens: int = DEFAULT_LMSTUDIO_MAX_TOKENS
    lmstudio_top_p: float = DEFAULT_LMSTUDIO_TOP_P
    lmstudio_top_k: int = DEFAULT_LMSTUDIO_TOP_K


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
    benchmark: dict[str, Any] = field(default_factory=dict)

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
            "benchmark": self.benchmark,
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
    cleaned = value.strip().lower().replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" \t\r\n,.;:!?\"'`()[]{}")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def strip_specific_locations(value: str) -> str:
    text = " ".join(value.strip().split())
    if not text:
        return ""

    cleaned_text = text
    for phrase in sorted(LOCATION_PHRASES, key=len, reverse=True):
        cleaned_text = re.sub(
            rf"\b{re.escape(phrase)}\b",
            " ",
            cleaned_text,
            flags=re.IGNORECASE,
        )

    tokens: list[str] = []
    for token in cleaned_text.split():
        stripped = token.strip(" .;:-")
        if not stripped:
            continue
        if stripped.lower() in LOCATION_TERMS:
            continue
        tokens.append(stripped)

    result = " ".join(tokens)
    result = re.sub(r"\s+", " ", result).strip(" ,.;:-")
    return result


def strip_non_ascii_text(value: str) -> str:
    if not value:
        return ""
    cleaned = NON_ASCII_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def clean_keyword_display(value: str) -> str:
    cleaned = value.strip().replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" \t\r\n,.;:!?\"'`()[]{}")
    return cleaned


def keyword_is_disallowed(normalized: str, original: str) -> bool:
    if not normalized:
        return True
    if NON_ASCII_PATTERN.search(original):
        return True
    if normalized in GENERIC_LOW_VALUE_KEYWORDS:
        return True
    if normalized in BLOCKED_IP_KEYWORDS:
        return True

    tokens = set(normalized.split())
    if tokens.intersection(BLOCKED_IP_KEYWORDS):
        return True

    technical_patterns = [
        r"\b(?:iso|exif|aperture|shutter|megapixel|resolution|white balance)\b",
        r"\b(?:camera|dslr|mirrorless|lens|focal length)\b",
        r"\b(?:jpeg|jpg|png|tiff|webp|raw|dng|heic)\b",
        r"\b\d{2,5}x\d{2,5}\b",
        r"\b\d{1,4}mm\b",
        r"\b(?:img|dsc|dscf|pxl|gopr)[-_]?\d+\b",
    ]
    if any(re.search(pattern, normalized) for pattern in technical_patterns):
        return True

    original_l = original.lower()
    if re.search(r"\bf/\d+(?:\.\d+)?\b", original_l):
        return True
    if re.search(r"\b\d+/\d+\b", original_l):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return True
    return False


def sanitize_title_text(raw_title: Any) -> str:
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    title = title.replace("_", " ").replace("-", " ")
    title = re.sub(r"\s+", " ", title).strip()
    title = strip_specific_locations(title)
    title = strip_non_ascii_text(title)
    title = re.sub(r"\s+", " ", title).strip(" ,.;:-")

    # Remove dangling prepositions after location stripping.
    title = re.sub(r"\b(in|at|from|near|around|across|within|inside|outside)\s*$", "", title).strip()
    return title


def extract_title_terms(title: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9']*", title.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokens:
        if token in TITLE_STOPWORDS or len(token) < 3:
            continue
        normalized = normalize_keyword(token)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return terms


def prioritize_title_terms(keywords: list[str], title: str) -> list[str]:
    title_terms = extract_title_terms(title)
    if not title_terms:
        return keywords

    by_normalized: dict[str, str] = {}
    ordered_normalized: list[str] = []
    for keyword in keywords:
        normalized = normalize_keyword(keyword)
        if not normalized or normalized in by_normalized:
            continue
        by_normalized[normalized] = keyword
        ordered_normalized.append(normalized)

    front: list[str] = []
    used: set[str] = set()
    for term in title_terms:
        if term in by_normalized and term not in used:
            front.append(by_normalized[term])
            used.add(term)

    tail: list[str] = []
    for normalized in ordered_normalized:
        if normalized in used:
            continue
        tail.append(by_normalized[normalized])
        used.add(normalized)

    return front + tail


def sanitize_keywords(raw_keywords: Any, title: str = "") -> list[str]:
    if isinstance(raw_keywords, str):
        candidates = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    elif isinstance(raw_keywords, list):
        candidates = [str(k).strip() for k in raw_keywords if str(k).strip()]
    else:
        candidates = []

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in candidates:
        cleaned = clean_keyword_display(keyword)
        cleaned = strip_specific_locations(cleaned)
        cleaned = strip_non_ascii_text(cleaned)
        normalized = normalize_keyword(cleaned)
        if not normalized:
            continue
        if keyword_is_disallowed(normalized, cleaned):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(cleaned)

    prioritized = prioritize_title_terms(deduped, title)
    # Keep Adobe-compatible upper bound while preserving strongest terms first.
    return prioritized[:MAX_KEYWORDS]


def validate_row(metadata: dict[str, Any], image_path: Path) -> RowData:
    filename = image_path.name.strip()
    if not filename:
        raise ValidationError("filename is empty")
    if filename != image_path.name:
        raise ValidationError("filename does not match source file")
    if not image_path.exists():
        raise ValidationError("source file does not exist")

    raw_title = metadata.get("title", "")
    title = sanitize_title_text(raw_title)
    if not title:
        raise ValidationError("title is empty")
    if "," in title:
        raise ValidationError("title contains comma")
    if len(title) > MAX_TITLE_LEN:
        raise ValidationError(f"title length exceeds {MAX_TITLE_LEN}")

    raw_keywords = metadata.get("keywords", [])
    keywords = sanitize_keywords(raw_keywords, title=title)
    if not keywords:
        raise ValidationError("keywords are empty")
    if len(keywords) < MIN_KEYWORDS:
        raise ValidationError(f"keyword count below minimum {MIN_KEYWORDS} after cleanup")

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


def enrich_keywords_if_needed(
    analyzer: ImageAnalyzer,
    image_path: Path,
    metadata: dict[str, Any],
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    title_raw = metadata.get("title", "")
    title = title_raw.strip() if isinstance(title_raw, str) else ""
    current_keywords = sanitize_keywords(metadata.get("keywords", []), title=title)

    enriched = dict(metadata)
    enriched["keywords"] = current_keywords
    if len(current_keywords) >= MIN_KEYWORDS:
        return enriched

    if not hasattr(analyzer, "enrich_keywords"):
        return enriched

    enrich_fn = getattr(analyzer, "enrich_keywords")
    if not callable(enrich_fn):
        return enriched

    try:
        raw_category = metadata.get("category")
        category_value = int(raw_category) if raw_category is not None else None
    except (TypeError, ValueError):
        category_value = None

    try:
        supplemental = enrich_fn(
            image_path,
            title,
            current_keywords,
            category_value,
        )
    except (AnalyzerUnavailableError, TypeError, ValueError) as exc:
        if logger:
            logger.log(f"enrich skip filename={image_path.name} reason={exc}")
        return enriched

    merged = current_keywords + supplemental
    enriched["keywords"] = sanitize_keywords(merged, title=title)
    if logger:
        logger.log(
            f"enrich filename={image_path.name} before={len(current_keywords)} after={len(enriched['keywords'])}"
        )
    return enriched


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


def slugify_model_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "model"


def resolve_output_dir(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir
    if args.backend == "lmstudio" and output_dir == Path("output"):
        return output_dir / "lmstudio" / slugify_model_name(args.lmstudio_model)
    return output_dir


def build_analyzer(config: RunConfig) -> ImageAnalyzer:
    backend = config.backend.strip().lower()
    if backend == "lmstudio":
        primary = LMStudioVisionAnalyzer(
            host=config.lmstudio_host,
            model=config.lmstudio_model,
            api_key=config.lmstudio_api_key,
            timeout_seconds=config.lmstudio_timeout_seconds,
            max_tokens=config.lmstudio_max_tokens,
            top_p=config.lmstudio_top_p,
            top_k=config.lmstudio_top_k,
        )
        fallback_model = config.lmstudio_fallback_model.strip()
        if fallback_model and fallback_model != config.lmstudio_model:
            fallback = LMStudioVisionAnalyzer(
                host=config.lmstudio_host,
                model=fallback_model,
                api_key=config.lmstudio_api_key,
                timeout_seconds=config.lmstudio_timeout_seconds,
                max_tokens=config.lmstudio_max_tokens,
                top_p=config.lmstudio_top_p,
                top_k=config.lmstudio_top_k,
            )
            return LMStudioFallbackAnalyzer(primary=primary, fallback=fallback)
        return primary
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


def build_category_resolver(config: RunConfig) -> CategoryResolver | None:
    backend = config.backend.strip().lower()
    if backend != "lmstudio":
        return None
    model = config.lmstudio_category_model.strip()
    if not model:
        return None
    return LMStudioVisionAnalyzer(
        host=config.lmstudio_host,
        model=model,
        api_key=config.lmstudio_api_key,
        timeout_seconds=config.lmstudio_timeout_seconds,
        max_tokens=min(120, config.lmstudio_max_tokens),
        top_p=config.lmstudio_top_p,
        top_k=config.lmstudio_top_k,
    )


def apply_category_resolution_if_needed(
    category_resolver: CategoryResolver | None,
    image_path: Path,
    metadata: dict[str, Any],
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    if category_resolver is None:
        return metadata
    title_raw = metadata.get("title", "")
    title = title_raw.strip() if isinstance(title_raw, str) else ""
    keywords = sanitize_keywords(metadata.get("keywords", []), title=title)
    if not title or not keywords:
        return metadata

    proposed_category: int | None = None
    raw_category = metadata.get("category")
    try:
        proposed_category = int(raw_category) if raw_category is not None else None
    except (TypeError, ValueError):
        proposed_category = None

    try:
        resolved = category_resolver.resolve_category(
            image_path=image_path,
            title=title,
            keywords=keywords,
            proposed_category=proposed_category,
        )
    except (AnalyzerUnavailableError, OSError, TimeoutError) as exc:
        if logger:
            logger.log(f"category-resolver skip filename={image_path.name} reason={exc}")
        return metadata

    if proposed_category != resolved and logger:
        logger.log(
            f"category-resolver override filename={image_path.name} from={proposed_category} to={resolved}"
        )
    enriched = dict(metadata)
    enriched["category"] = resolved
    return enriched


def run_batch(
    config: RunConfig,
    analyzer: ImageAnalyzer | None = None,
    category_resolver: CategoryResolver | None = None,
) -> RunStats:
    portfolio_dir = config.portfolio_dir
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "adobe_stock_upload.csv"
    review_path = output_dir / "review_needed.csv"
    progress_path = output_dir / "progress.json"
    log_path = output_dir / "run.log"

    logger = RunLogger(log_path, verbose=config.verbose)
    stats = RunStats(started_at=now_iso())
    benchmark = BenchmarkTracker()

    try:
        if not portfolio_dir.exists() or not portfolio_dir.is_dir():
            raise ValueError(f"Portfolio directory not found: {portfolio_dir}")

        image_paths = discover_images(portfolio_dir)
        stats.total_images = len(image_paths)
        processed = ensure_csv_header(csv_path, HEADER)
        ensure_review_csv(review_path)

        analyzer_was_built = analyzer is None
        if analyzer is None:
            analyzer = build_analyzer(config)
        if category_resolver is None and analyzer_was_built:
            category_resolver = build_category_resolver(config)

        processed_before_run = set(processed)
        logger.log(f"start total_images={stats.total_images} processed_existing={len(processed)}")
        logger.log(
            "benchmark fields=image_count,analysis_avg_seconds,analysis_p50_seconds,"
            "analysis_p95_seconds,analysis_max_seconds,total_avg_seconds,total_p95_seconds,"
            "total_max_seconds,throughput_images_per_min,warmup_first_image_seconds"
        )
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

                image_started = time.perf_counter()
                analysis_started = time.perf_counter()
                analysis_seconds = 0.0
                try:
                    metadata = analyzer.analyze_image(image_path)
                    metadata = enrich_keywords_if_needed(analyzer, image_path, metadata, logger=logger)
                    metadata = apply_category_resolution_if_needed(
                        category_resolver,
                        image_path,
                        metadata,
                        logger=logger,
                    )
                    analysis_seconds = time.perf_counter() - analysis_started
                    row = validate_row(metadata, image_path)
                except (AnalyzerUnavailableError, ValidationError, TimeoutError, OSError) as exc:
                    reason = str(exc)
                    if analysis_seconds == 0.0:
                        analysis_seconds = time.perf_counter() - analysis_started
                    total_seconds = time.perf_counter() - image_started
                    benchmark.add(filename, analysis_seconds, total_seconds)
                    stats.benchmark = benchmark.summary()
                    append_review(review_writer, review_fp, filename, reason)
                    stats.failed += 1
                    stats.analyzed += 1
                    stats.last_filename = filename
                    logger.log(f"review filename={filename} reason={reason}")
                    logger.log(
                        f"perf filename={filename} analysis_s={analysis_seconds:.2f} total_s={total_seconds:.2f}"
                    )
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

                total_seconds = time.perf_counter() - image_started
                benchmark.add(filename, analysis_seconds, total_seconds)
                stats.benchmark = benchmark.summary()
                stats.analyzed += 1
                stats.last_filename = filename
                logger.log(
                    f"perf filename={filename} analysis_s={analysis_seconds:.2f} total_s={total_seconds:.2f}"
                )
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
        stats.benchmark = benchmark.summary()
        logger.log(
            "benchmark summary "
            f"count={stats.benchmark.get('image_count', 0)} "
            f"avg_total_s={stats.benchmark.get('total_avg_seconds', 0.0):.2f} "
            f"p95_total_s={stats.benchmark.get('total_p95_seconds', 0.0):.2f} "
            f"max_total_s={stats.benchmark.get('total_max_seconds', 0.0):.2f} "
            f"slowest_total={stats.benchmark.get('total_slowest_filename', '')} "
            f"avg_analysis_s={stats.benchmark.get('analysis_avg_seconds', 0.0):.2f} "
            f"p95_analysis_s={stats.benchmark.get('analysis_p95_seconds', 0.0):.2f} "
            f"throughput_per_min={stats.benchmark.get('throughput_images_per_min', 0.0):.2f}"
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
        "--lmstudio-fallback-model",
        type=str,
        default=os.getenv("LMSTUDIO_FALLBACK_MODEL", DEFAULT_LMSTUDIO_FALLBACK_MODEL),
        help="Fallback LM Studio model if primary LM Studio model is unavailable",
    )
    parser.add_argument(
        "--lmstudio-category-model",
        type=str,
        default=os.getenv("LMSTUDIO_CATEGORY_MODEL", DEFAULT_LMSTUDIO_CATEGORY_MODEL),
        help="LM Studio model dedicated for final category selection (set empty to disable)",
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
        "--lmstudio-timeout-seconds",
        type=int,
        default=int(os.getenv("LMSTUDIO_TIMEOUT_SECONDS", str(DEFAULT_LMSTUDIO_TIMEOUT_SECONDS))),
        help="LM Studio request timeout in seconds",
    )
    parser.add_argument(
        "--lmstudio-max-tokens",
        type=int,
        default=int(os.getenv("LMSTUDIO_MAX_TOKENS", str(DEFAULT_LMSTUDIO_MAX_TOKENS))),
        help="LM Studio max tokens per response",
    )
    parser.add_argument(
        "--lmstudio-top-p",
        type=float,
        default=float(os.getenv("LMSTUDIO_TOP_P", str(DEFAULT_LMSTUDIO_TOP_P))),
        help="LM Studio top-p sampling value",
    )
    parser.add_argument(
        "--lmstudio-top-k",
        type=int,
        default=int(os.getenv("LMSTUDIO_TOP_K", str(DEFAULT_LMSTUDIO_TOP_K))),
        help="LM Studio top-k sampling value",
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
    output_dir = resolve_output_dir(args)

    if args.validate_only:
        csv_path = output_dir / "adobe_stock_upload.csv"
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
                output_dir=output_dir,
                limit=args.limit,
                model=args.model,
                dry_run=args.dry_run,
                verbose=args.verbose,
                backend=args.backend,
                ollama_model=args.ollama_model,
                ollama_host=args.ollama_host,
                lmstudio_model=args.lmstudio_model,
                lmstudio_fallback_model=args.lmstudio_fallback_model,
                lmstudio_category_model=args.lmstudio_category_model,
                lmstudio_host=args.lmstudio_host,
                lmstudio_api_key=args.lmstudio_api_key,
                lmstudio_timeout_seconds=args.lmstudio_timeout_seconds,
                lmstudio_max_tokens=args.lmstudio_max_tokens,
                lmstudio_top_p=args.lmstudio_top_p,
                lmstudio_top_k=args.lmstudio_top_k,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        "Run finished: "
        f"written={stats.written} failed={stats.failed} analyzed={stats.analyzed} "
        f"skipped_existing={stats.skipped_existing} skipped_collision={stats.skipped_collision} "
        f"avg_image_s={stats.benchmark.get('total_avg_seconds', 0.0):.2f} "
        f"p95_image_s={stats.benchmark.get('total_p95_seconds', 0.0):.2f} "
        f"slowest={stats.benchmark.get('total_slowest_filename', '')}:"
        f"{stats.benchmark.get('total_max_seconds', 0.0):.2f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
