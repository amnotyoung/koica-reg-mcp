import json
import os
import tempfile
import unittest
from contextlib import ExitStack
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
    @patch("alio_sync.subprocess.run")
    @patch("alio_sync.shutil.which")
    def test_kordoc_uses_preinstalled_binary(self, which, run):
        which.return_value = "/usr/local/bin/kordoc"
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hwp = root / "hwp"
            md = root / "md"
            hwp.mkdir()
            (hwp / "123.hwp").write_bytes(b"HWP")
            with (
                patch.object(alio_sync, "HWP_CACHE", hwp),
                patch.object(alio_sync, "MD_CACHE", md),
            ):
                alio_sync.kordoc_convert([{
                    "file_no": "123",
                    "ext": "hwp",
                }])

        self.assertEqual(run.call_args.args[0][0], "/usr/local/bin/kordoc")

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


def _copy(number: int) -> str:
    return "\n".join([
        "제정 1998.04.29. 예규 제 10호",
        "",
        "개정 2025.02.17. 지침 제500호",
        "",
        "## 제1장 총칙",
        "",
        "제1조(목적) 이 규정은 내규 관리에 필요한 사항을 정한다.",
        "",
        "### 제9조(입안예고) 입안예고 기간은 7일 이상으로 한다.",
        "",
        "[별표 1] 평가 기준",
        # 원본 HWP의 구역마다 자동 번호가 이어 매겨지는 칸
        f"<tr><td>{number}. 업무해태 가능성</td></tr>",
        "",
        "[별지 제1호서식] 비공개 요청서",
        "",
    ])


class RepeatedDocumentTests(unittest.TestCase):
    HEADER = "# 내규관리규정 (2025.02.17 개정)"

    def test_keeps_only_first_copy_of_repeated_document(self):
        md = "\n".join([self.HEADER, "", _copy(1), _copy(2), _copy(3)])

        text, copies, collapsed = alio_sync.collapse_repeated_document(md)

        self.assertTrue(collapsed)
        self.assertEqual(copies, 3)
        self.assertEqual(text, "\n".join([self.HEADER, "", _copy(1)]).rstrip("\n"))
        self.assertEqual(text.count("제1조(목적)"), 1)

    def test_keeps_repeated_forms_without_repeated_history(self):
        form = "[별지 제2호서식] 계약서\n\n제1조(목적) 계약 목적\n\n제2조(기간) 계약 기간\n"
        md = "\n".join([self.HEADER, "", _copy(1), form, form, form])

        self.assertEqual(alio_sync.collapse_repeated_document(md), (md, 1, False))

    def test_keeps_document_when_repeated_copies_differ(self):
        changed = _copy(2).replace("7일 이상", "10일 이상").replace("내규 관리", "규정 관리")
        changed = changed.replace("비공개 요청서", "공개 요청서").replace("평가 기준", "검토 기준")
        md = "\n".join([self.HEADER, "", _copy(1), changed])

        self.assertEqual(alio_sync.collapse_repeated_document(md), (md, 2, False))

    def test_keeps_document_when_copy_lacks_first_article(self):
        partial = "제정 1998.04.29. 예규 제 10호\n\n부칙만 있는 다른 문서"
        md = "\n".join([self.HEADER, "", _copy(1), partial])

        self.assertEqual(alio_sync.collapse_repeated_document(md), (md, 2, False))


class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.extracted = root / "extracted"
        self.raw = root / "md_raw"
        self.raw.mkdir()
        self.manifest = root / "sources.json"
        for name, value in {
            "DATA_DIR": root,
            "EXTRACT_DIR": self.extracted,
            "MD_CACHE": self.raw,
            "SOURCES_PATH": self.manifest,
            "MIN_SOURCE_COUNT": 1,
        }.items():
            stack.enter_context(patch.object(alio_sync, name, value))
        self.resolved = [{
            "title": title, "type": "규정", "file_no": str(i),
            "revision": "2026.07.29",
        } for i, title in enumerate(["인사", "보수"], 1)]
        for r in self.resolved:
            (self.raw / f"{r['file_no']}.md").write_text("기존 본문", encoding="utf-8")
        alio_sync.write_extracts_and_sources(self.resolved)
        # 과거 시각을 고정해 파일시스템의 시각 정밀도와 무관하게 재기록을 감지한다.
        self.original_mtimes = {}
        for path in [*self.extracted.glob("*.md"), self.manifest]:
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
            self.original_mtimes[path] = path.stat().st_mtime_ns

    def test_identical_sync_preserves_files_and_manifest(self):
        alio_sync.write_extracts_and_sources(self.resolved)

        for path, mtime in self.original_mtimes.items():
            self.assertEqual(path.stat().st_mtime_ns, mtime, path.name)

    def test_content_change_and_addition_preserve_unchanged_and_unmanaged_files(self):
        (self.raw / "2.md").write_text("변경된 본문", encoding="utf-8")
        (self.raw / "3.md").write_text("신규 본문", encoding="utf-8")
        self.resolved.append({
            "title": "복무", "type": "규정", "file_no": "3",
            "revision": "2026.07.29",
        })
        personal = self.extracted / "개인메모.md"
        personal.write_text("유지", encoding="utf-8")

        alio_sync.write_extracts_and_sources(self.resolved)

        unchanged = self.extracted / "규정_인사.md"
        self.assertEqual(unchanged.stat().st_mtime_ns, self.original_mtimes[unchanged])
        self.assertIn("변경된 본문", (self.extracted / "규정_보수.md").read_text())
        self.assertIn("신규 본문", (self.extracted / "규정_복무.md").read_text())
        self.assertEqual(personal.read_text(), "유지")
        self.assertEqual(json.loads(self.manifest.read_text())["count"], 3)

    def test_collection_date_and_page_follow_content_changes(self):
        manifest = json.loads(self.manifest.read_text())
        for source in manifest["sources"]:
            source["collected_at"] = "2026-01-02"
            source["page_url"] = "https://www.alio.go.kr/item/old"
        self.manifest.write_text(json.dumps(manifest, ensure_ascii=False))
        (self.raw / "2.md").write_text("변경된 본문", encoding="utf-8")
        self.resolved[1]["page_url"] = "https://www.alio.go.kr/item/new"

        alio_sync.write_extracts_and_sources(self.resolved)

        by_name = {s["name"]: s for s in json.loads(self.manifest.read_text())["sources"]}
        self.assertEqual(by_name["인사"]["collected_at"], "2026-01-02")
        self.assertEqual(by_name["인사"]["page_url"], "https://www.alio.go.kr/item/old")
        self.assertNotEqual(by_name["보수"]["collected_at"], "2026-01-02")
        self.assertRegex(by_name["보수"]["collected_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(by_name["보수"]["page_url"], "https://www.alio.go.kr/item/new")

    def test_repeated_document_is_written_once_and_logged(self):
        (self.raw / "2.md").write_text("# 보수\n\n" + _copy(1) + _copy(2), encoding="utf-8")

        with patch("alio_sync.log") as log:
            alio_sync.write_extracts_and_sources(self.resolved)

        written = (self.extracted / "규정_보수.md").read_text(encoding="utf-8")
        self.assertEqual(written.count("제1조(목적)"), 1)
        self.assertIn("1. 업무해태 가능성", written)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any("문서 전체 반복 정리: 보수" in m and "2벌" in m for m in messages))

    def test_type_change_removes_only_superseded_filename(self):
        self.resolved[0]["type"] = "지침"

        alio_sync.write_extracts_and_sources(self.resolved)

        self.assertFalse((self.extracted / "규정_인사.md").exists())
        self.assertTrue((self.extracted / "지침_인사.md").exists())
        unchanged = self.extracted / "규정_보수.md"
        self.assertEqual(unchanged.stat().st_mtime_ns, self.original_mtimes[unchanged])


if __name__ == "__main__":
    unittest.main()
