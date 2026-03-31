import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.adobe_stock_csv_cli import (
    AnalyzerUnavailableError,
    BenchmarkTracker,
    HEADER,
    DEFAULT_OPENAI_MODEL,
    LMStudioFallbackAnalyzer,
    LMStudioVisionAnalyzer,
    build_analyzer,
    parse_args,
    RunConfig,
    quick_validate_csv_structure,
    run_batch,
    sanitize_keywords,
    resolve_output_dir,
)


class StubAnalyzer:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def analyze_image(self, image_path: Path):
        self.calls.append(image_path.name)
        value = self.mapping.get(image_path.name)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return {
                "title": "Default title",
                "keywords": kw("landscape", "nature", "outdoor"),
                "category": 11,
                "releases": "",
            }
        return value


def make_image(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fakejpg")


def kw(*head: str, size: int = 15) -> list[str]:
    values: list[str] = []
    for item in head:
        if item not in values:
            values.append(item)
    i = 1
    while len(values) < size:
        values.append(f"detail{i}")
        i += 1
    return values


class AdobeStockCsvCliTests(unittest.TestCase):
    def test_first_run_creates_outputs_and_writes_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "a.jpg")
            make_image(portfolio / "b.jpg")

            analyzer = StubAnalyzer(
                {
                    "a.jpg": {
                        "title": "Mountain lake at sunrise",
                        "keywords": kw("landscape", "mountain", "lake", "sunrise"),
                        "category": 11,
                        "releases": "",
                    },
                    "b.jpg": {
                        "title": "Orange cat portrait",
                        "keywords": kw("animal", "cat", "pet", "portrait"),
                        "category": 1,
                        "releases": "",
                    },
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.written, 2)
            self.assertTrue((output / "adobe_stock_upload.csv").exists())
            self.assertTrue((output / "review_needed.csv").exists())
            self.assertTrue((output / "progress.json").exists())
            self.assertTrue((output / "run.log").exists())

            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[0], HEADER)
            self.assertEqual(len(rows), 3)

    def test_resume_skips_existing_filename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            output.mkdir(parents=True, exist_ok=True)

            make_image(portfolio / "a.jpg")
            make_image(portfolio / "b.jpg")

            with (output / "adobe_stock_upload.csv").open("w", encoding="utf-8", newline="") as fp:
                writer = csv.writer(fp, lineterminator="\n")
                writer.writerow(HEADER)
                writer.writerow(["a.jpg", "Already done", "landscape", "11", ""])

            analyzer = StubAnalyzer(
                {
                    "b.jpg": {
                        "title": "Forest trail in morning fog",
                        "keywords": kw("landscape", "forest", "trail", "fog"),
                        "category": 11,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.skipped_existing, 1)
            self.assertEqual(stats.written, 1)
            self.assertEqual(analyzer.calls, ["b.jpg"])

    def test_invalid_metadata_goes_to_review_and_continues(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "bad.jpg")
            make_image(portfolio / "good.jpg")

            analyzer = StubAnalyzer(
                {
                    "bad.jpg": {
                        "title": "Invalid, comma title",
                        "keywords": kw("landscape"),
                        "category": 11,
                        "releases": "",
                    },
                    "good.jpg": {
                        "title": "Waterfall in tropical forest",
                        "keywords": kw("waterfall", "forest", "nature", "landscape"),
                        "category": 11,
                        "releases": "",
                    },
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.failed, 1)
            self.assertEqual(stats.written, 1)

            with (output / "review_needed.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[0], ["Filename", "Reason"])
            self.assertEqual(rows[1][0], "bad.jpg")
            self.assertIn("title contains comma", rows[1][1])

    def test_duplicate_basename_collision_logged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "a" / "same.jpg")
            make_image(portfolio / "b" / "same.jpg")

            analyzer = StubAnalyzer(
                {
                    "same.jpg": {
                        "title": "City skyline at dusk",
                        "keywords": kw("city", "skyline", "architecture", "dusk"),
                        "category": 2,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.written, 1)
            self.assertEqual(stats.skipped_collision, 1)

            with (output / "review_needed.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[1][0], "same.jpg")
            self.assertIn("duplicate basename collision", rows[1][1])

    def test_keyword_duplicate_normalization_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "dup.jpg")

            analyzer = StubAnalyzer(
                {
                    "dup.jpg": {
                        "title": "Leaf close up with dew",
                        "keywords": kw(
                            "Leaf",
                            " leaf ",
                            "LEAF",
                            "plant",
                            "botanical",
                            "macro",
                            "dew",
                            "texture",
                            "green",
                            "nature",
                            "freshness",
                            "close up",
                            "flora",
                            "detail",
                            "outdoors",
                            "natural",
                            "garden",
                        ),
                        "category": 14,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.written, 1)
            self.assertEqual(stats.failed, 0)

            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            # Duplicates should be auto-cleaned while keeping a valid keyword set.
            keywords = [k.strip() for k in rows[1][2].split(",") if k.strip()]
            self.assertIn("Leaf", keywords)
            self.assertNotIn("leaf", keywords[1:])  # first normalized term kept, repeats removed
            self.assertGreaterEqual(len(keywords), 15)

    def test_keywords_over_49_are_truncated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "many.jpg")

            many_keywords = [f"kw{i}" for i in range(60)]
            analyzer = StubAnalyzer(
                {
                    "many.jpg": {
                        "title": "Busy street market",
                        "keywords": many_keywords,
                        "category": 12,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(
                    portfolio_dir=portfolio,
                    output_dir=output,
                    limit=None,
                    model=DEFAULT_OPENAI_MODEL,
                    dry_run=False,
                    verbose=False,
                ),
                analyzer=analyzer,
            )

            self.assertEqual(stats.written, 1)
            self.assertEqual(stats.failed, 0)

            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            keyword_count = len([k.strip() for k in rows[1][2].split(",") if k.strip()])
            self.assertEqual(keyword_count, 49)

    def test_quick_validate_structure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            csv_path = root / "adobe_stock_upload.csv"

            with csv_path.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.writer(fp, lineterminator="\n")
                writer.writerow(HEADER)
                writer.writerow(["a.jpg", "Valid title", "landscape, nature", "11", ""])

            ok, lines = quick_validate_csv_structure(csv_path, preview_lines=2)
            self.assertTrue(ok)
            self.assertEqual(lines[0], "Filename,Title,Keywords,Category,Releases")
            self.assertTrue(lines[1].startswith("a.jpg,"))

            with (root / "progress.json").open("w", encoding="utf-8") as fp:
                json.dump({"done": True}, fp)

    def test_parse_args_defaults_to_lmstudio_backend(self):
        args = parse_args([])
        self.assertEqual(args.backend, "lmstudio")

    def test_lmstudio_unavailable_goes_to_review_and_continues(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "a.jpg")
            make_image(portfolio / "b.jpg")

            stub = StubAnalyzer(
                {
                    "a.jpg": AnalyzerUnavailableError("LM Studio network error: offline"),
                    "b.jpg": {
                        "title": "Waterfall over rocky cliff",
                        "keywords": kw("waterfall", "nature", "landscape", "rock"),
                        "category": 11,
                        "releases": "",
                    },
                }
            )

            with mock.patch("src.adobe_stock_csv_cli.build_analyzer", return_value=stub):
                stats = run_batch(
                    RunConfig(
                        portfolio_dir=portfolio,
                        output_dir=output,
                        backend="lmstudio",
                    ),
                    analyzer=None,
                )

            self.assertEqual(stats.failed, 1)
            self.assertEqual(stats.written, 1)
            self.assertEqual(stub.calls, ["a.jpg", "b.jpg"])

            with (output / "review_needed.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[1][0], "a.jpg")
            self.assertIn("LM Studio network error: offline", rows[1][1])

    def test_build_analyzer_returns_lmstudio_by_default(self):
        analyzer = build_analyzer(RunConfig(portfolio_dir=Path("."), output_dir=Path(".")))
        self.assertEqual(analyzer.__class__.__name__, "LMStudioFallbackAnalyzer")

    def test_title_terms_are_prioritized_in_keywords(self):
        keywords = sanitize_keywords(
            [
                "landscape",
                "nature",
                "sunset",
                "waterfall",
                "forest",
                "travel",
                "outdoors",
                "scenic",
                "mountain",
                "river",
                "viewpoint",
                "destination",
                "hiking",
                "adventure",
                "tranquil",
            ],
            title="Waterfall at sunset in forest",
        )
        self.assertGreaterEqual(len(keywords), 15)
        self.assertEqual(keywords[0].lower(), "waterfall")
        self.assertIn("sunset", [k.lower() for k in keywords[:3]])
        self.assertIn("forest", [k.lower() for k in keywords[:4]])

    def test_low_keyword_count_uses_enrichment_retry(self):
        class EnrichingAnalyzer:
            def analyze_image(self, image_path: Path):
                return {
                    "title": "Scenic waterfall in forest valley",
                    "keywords": ["waterfall", "forest", "nature", "landscape", "scenic"],
                    "category": 11,
                    "releases": "",
                }

            def enrich_keywords(self, image_path: Path, title: str, existing: list[str], category):
                return [
                    "travel",
                    "outdoors",
                    "river",
                    "mountain",
                    "destination",
                    "adventure",
                    "hiking",
                    "valley",
                    "green",
                    "mist",
                    "cascade",
                ]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "a.jpg")

            stats = run_batch(
                RunConfig(portfolio_dir=portfolio, output_dir=output),
                analyzer=EnrichingAnalyzer(),
            )

            self.assertEqual(stats.written, 1)
            self.assertEqual(stats.failed, 0)
            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            keywords = [k.strip() for k in rows[1][2].split(",") if k.strip()]
            self.assertGreaterEqual(len(keywords), 15)

    def test_lmstudio_fallback_analyzer_uses_fallback_model(self):
        class Primary:
            model = "qwen/qwen3-vl-8b"

            def analyze_image(self, image_path: Path):
                raise AnalyzerUnavailableError("primary unavailable")

            def enrich_keywords(self, image_path: Path, title: str, existing: list[str], category):
                raise AnalyzerUnavailableError("primary unavailable")

        class Fallback:
            model = "google/gemma-3-4b"

            def analyze_image(self, image_path: Path):
                return {"title": "ok", "keywords": kw("nature"), "category": 11, "releases": ""}

            def enrich_keywords(self, image_path: Path, title: str, existing: list[str], category):
                return []

        wrapped = LMStudioFallbackAnalyzer(primary=Primary(), fallback=Fallback())
        result = wrapped.analyze_image(Path("dummy.jpg"))
        self.assertEqual(result["title"], "ok")

    def test_resolve_output_dir_defaults_to_model_subfolder(self):
        args = parse_args(
            [
                "--backend",
                "lmstudio",
                "--lmstudio-model",
                "qwen/qwen3-vl-8b",
            ]
        )
        resolved = resolve_output_dir(args)
        self.assertEqual(resolved, Path("output/lmstudio/qwen-qwen3-vl-8b"))

    def test_category_can_be_overridden_by_category_resolver(self):
        class Resolver:
            def resolve_category(self, image_path: Path, title: str, keywords: list[str], proposed_category=None):
                return 14

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "flower.jpg")

            analyzer = StubAnalyzer(
                {
                    "flower.jpg": {
                        "title": "Pink flower close up",
                        "keywords": kw("flower", "plant", "petal", "macro"),
                        "category": 1,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(portfolio_dir=portfolio, output_dir=output),
                analyzer=analyzer,
                category_resolver=Resolver(),
            )
            self.assertEqual(stats.written, 1)

            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[1][3], "14")

    def test_category_resolver_failure_keeps_original_category(self):
        class Resolver:
            def resolve_category(self, image_path: Path, title: str, keywords: list[str], proposed_category=None):
                raise AnalyzerUnavailableError("resolver offline")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "Portfolio"
            output = root / "output"
            make_image(portfolio / "cat.jpg")

            analyzer = StubAnalyzer(
                {
                    "cat.jpg": {
                        "title": "Orange cat portrait",
                        "keywords": kw("cat", "animal", "pet", "portrait"),
                        "category": 1,
                        "releases": "",
                    }
                }
            )

            stats = run_batch(
                RunConfig(portfolio_dir=portfolio, output_dir=output),
                analyzer=analyzer,
                category_resolver=Resolver(),
            )
            self.assertEqual(stats.written, 1)

            with (output / "adobe_stock_upload.csv").open("r", encoding="utf-8", newline="") as fp:
                rows = list(csv.reader(fp))
            self.assertEqual(rows[1][3], "1")


if __name__ == "__main__":
    unittest.main()
