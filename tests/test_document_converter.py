"""Verify the upgraded CLI against a public ALIO HWP, offline."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "tools/document-converter/node_modules/.bin/kordoc"
CONVERTER = str(LOCAL) if LOCAL.exists() else shutil.which("kordoc")


class DocumentConverterTests(unittest.TestCase):
    @unittest.skipUnless(CONVERTER or os.environ.get("CI"), "Install the locked document converter to run this integration test")
    def test_public_hwp_keeps_articles_and_appendix(self):
        self.assertIsNotNone(CONVERTER, "The CI/production image must include the converter")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([
                CONVERTER, str(ROOT / "tests/fixtures/alio-238825.hwp"),
                "-d", tmp, "--silent",
            ], check=True, timeout=60, capture_output=True, text=True)
            text = (Path(tmp) / "alio-238825.md").read_text()
        for required in ["제1조(목적)", "제7조(보직의 특례)", "업무분장", "별표"]:
            self.assertIn(required, text)
        self.assertGreater(len(text), 50000)
