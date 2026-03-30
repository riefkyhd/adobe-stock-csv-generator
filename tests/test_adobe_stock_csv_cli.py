import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.adobe_stock_csv_cli import (
    AnalyzerUnavailableError,
    HEADER,
    DEFAULT_OPENAI_MODEL,
    build_analyzer,
    parse_args,
    RunConfig,
    quick_validate_csv_structure,
    run_batch,
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
                "keywords": ["landscape", "nature", "outdoor"],
                "category": 11,
                "releases": "",
            }
        return value


def make_image(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fakejpg")


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
                        "keywords": ["landscape", "mountain", "lake", "sunrise"],
                        "category": 11,
                        "releases": "",
                    },
                    "b.jpg": {
                        "title": "Orange cat portrait",
                        "keywords": ["animal", "cat", "pet", "portrait"],
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
                        "keywords": ["landscape", "forest", "trail", "fog"],
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
                        "keywords": ["landscape"],
                        "category": 11,
                        "releases": "",
                    },
                    "good.jpg": {
                        "title": "Waterfall in tropical forest",
                        "keywords": ["waterfall", "forest", "nature", "landscape"],
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
                        "keywords": ["city", "skyline", "architecture", "dusk"],
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
                        "keywords": ["Leaf", " leaf ", "LEAF"],
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

            self.assertEqual(stats.written, 0)
            self.assertEqual(stats.failed, 1)

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
                        "keywords": ["waterfall", "nature", "landscape", "rock"],
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
        self.assertEqual(analyzer.__class__.__name__, "LMStudioVisionAnalyzer")


if __name__ == "__main__":
    unittest.main()
