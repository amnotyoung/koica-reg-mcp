import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import alio_sync


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


class HttpRetryTests(unittest.TestCase):
    @patch("alio_sync.urllib.request.urlopen")
    def test_get_binary_returns_bytes_and_headers(self, urlopen):
        urlopen.return_value = FakeResponse(b"\x00HWP")

        body, headers = alio_sync.http_get(
            "https://example.test/file.hwp",
            binary=True,
            retries=1,
        )

        self.assertEqual(body, b"\x00HWP")
        self.assertEqual(headers, {})

    @patch("alio_sync._retry_delay")
    @patch("alio_sync.urllib.request.urlopen")
    def test_post_retries_transient_timeout(self, urlopen, _delay):
        urlopen.side_effect = [
            URLError("timed out"),
            FakeResponse(b'{"data": {"ok": true}}'),
        ]

        result = alio_sync.http_post_json("/test", {"a": 1}, retries=2)

        self.assertTrue(result["data"]["ok"])
        self.assertEqual(urlopen.call_count, 2)

    @patch("alio_sync._retry_delay")
    @patch("alio_sync.urllib.request.urlopen")
    def test_post_reports_failure_after_all_retries(self, urlopen, _delay):
        urlopen.side_effect = URLError("timed out")

        with self.assertRaisesRegex(RuntimeError, "POST 실패"):
            alio_sync.http_post_json("/test", {}, retries=3)

        self.assertEqual(urlopen.call_count, 3)


class SafetyTests(unittest.TestCase):
    def test_rejects_removed_regulation(self):
        previous = [
            {"name": "규정 A", "revision": "2026.01.01", "origin": "alio"},
            {"name": "규정 B", "revision": "2026.01.01", "origin": "alio"},
        ]
        current = [
            {"name": "규정 A", "revision": "2026.02.01", "origin": "alio"},
        ]

        with self.assertRaisesRegex(RuntimeError, "자동 삭제하지 않고"):
            alio_sync.validate_manifest_update(previous, current)

    def test_rejects_revision_regression(self):
        previous = [
            {"name": "규정 A", "revision": "2026.07.01", "origin": "alio"},
        ]
        current = [
            {"name": "규정 A", "revision": "2026.06.01", "origin": "alio"},
        ]

        with self.assertRaisesRegex(RuntimeError, "개정일이 역행"):
            alio_sync.validate_manifest_update(previous, current)

    def test_accepts_updates_and_additions(self):
        previous = [
            {"name": "규정 A", "revision": "2026.01.01", "origin": "alio"},
        ]
        current = [
            {"name": "규정 A", "revision": "2026.02.01", "origin": "alio"},
            {"name": "규정 B", "revision": "2026.01.01", "origin": "alio"},
        ]

        alio_sync.validate_manifest_update(previous, current)

    @patch("alio_sync.time.sleep")
    @patch("alio_sync.http_get")
    def test_download_writes_only_response_body(self, http_get, _sleep):
        http_get.return_value = (b"HWP bytes", {"Content-Type": "application/octet-stream"})
        with tempfile.TemporaryDirectory() as tmp:
            hwp_cache = Path(tmp)
            with patch.object(alio_sync, "HWP_CACHE", hwp_cache):
                alio_sync.download_files([{
                    "file_no": "123",
                    "ext": "hwp",
                    "title": "테스트 규정",
                }])

            self.assertEqual((hwp_cache / "123.hwp").read_bytes(), b"HWP bytes")

    def test_rejects_truncated_collection(self):
        previous = [{"origin": "alio"} for _ in range(149)]

        with self.assertRaisesRegex(RuntimeError, "기존 데이터를 유지"):
            alio_sync.validate_collection(
                items=[{} for _ in range(149)],
                resolved=[{} for _ in range(20)],
                previous_sources=previous,
            )

    def test_failed_conversion_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "extracted"
            raw = root / "md_raw"
            extracted.mkdir()
            raw.mkdir()
            existing = extracted / "규정_기존.md"
            existing.write_text("기존 본문", encoding="utf-8")
            sources_path = root / "sources.json"
            sources_path.write_text(json.dumps({
                "sources": [{
                    "name": "기존",
                    "file": existing.name,
                    "origin": "alio",
                }]
            }), encoding="utf-8")
            previous = [{
                "name": "기존",
                "file": existing.name,
                "origin": "alio",
            }]

            with (
                patch.object(alio_sync, "DATA_DIR", root),
                patch.object(alio_sync, "EXTRACT_DIR", extracted),
                patch.object(alio_sync, "MD_CACHE", raw),
                patch.object(alio_sync, "SOURCES_PATH", sources_path),
                patch.object(alio_sync, "MIN_SOURCE_COUNT", 1),
            ):
                with self.assertRaisesRegex(RuntimeError, "기존 데이터를 유지"):
                    alio_sync.write_extracts_and_sources(
                        resolved=[{
                            "title": "새 규정",
                            "type": "규정",
                            "file_no": "1",
                            "revision": "2026.07.29",
                        }],
                        previous_sources=previous,
                    )

            self.assertEqual(existing.read_text(encoding="utf-8"), "기존 본문")
            self.assertTrue(sources_path.exists())


if __name__ == "__main__":
    unittest.main()
