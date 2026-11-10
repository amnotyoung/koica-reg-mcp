"""Offline regression checks for the scheduled law review."""
import copy
import socket
import ssl
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch

from lxml import html

from sync import (Edition, IncompleteSourceResponse, SourceIssue, current_edition, fetch,
                  result_code, source_text, sync, temporary_source_error, update_excerpt)

BODY = html.fromstring('''<div class="lawcon">
<p><input name="joNoList" id="Y000700"><label>제7조(방법)</label>① 첫째</p>
<p>1. 첫째 항의 호</p><p>가. 첫째 목</p><p>나. 둘째 목</p>
<p>② 둘째</p><p>1. 둘째 항의 호</p><p>2. 둘째 항의 다른 호</p>
<p><span class="sfon">[개정]</span></p></div>''')
OLD = {"text": "이전 문구", "version": "2025.01.01 시행",
       "url": "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=1#J7:0"}
NEW = Edition(False, "000123", "2", "20260101", "테스트법")


class WeeklyLawTests(unittest.TestCase):
    def test_exact_subunit_never_includes_next_parent_or_annotations(self):
        self.assertEqual(source_text(BODY, "제7조제1항제1호나목"), "나. 둘째 목")
        self.assertEqual(source_text(BODY, "제7조제2항제2호"), "2. 둘째 항의 다른 호")
        self.assertIsNone(source_text(BODY, "제7조제1호"))
        self.assertIsNone(source_text(BODY, "제8조"))

    def test_changed_text_and_idempotent_second_read(self):
        result, reason = update_excerpt(OLD, "테스트법", "제7조제2항", NEW, lambda: BODY)
        self.assertTrue(result["text"].startswith("② 둘째"))
        self.assertIn("재검토", reason)
        self.assertEqual(update_excerpt(result, "테스트법", "제7조제2항", NEW, None)[0], result)

    def test_removal_or_rename_preserves_verified_excerpt(self):
        for title, coord in [("테스트법", "제8조"), ("예전법", "제7조")]:
            result, _ = update_excerpt(OLD, title, coord, NEW, lambda: BODY)
            for key in OLD:
                self.assertEqual(result[key], OLD[key])
            self.assertIn("lsiSeq=2", result["sync_review"]["url"])

    def test_regressive_edition_is_never_published(self):
        with self.assertRaisesRegex(ValueError, "과거"):
            update_excerpt(dict(OLD, version="2026.02.01 시행"), "테스트법", "제7조", NEW, None)

    def test_failed_fetch_preserves_data_and_historical_version_is_never_queried(self):
        laws = {"laws": {"테스트법": {"lawId": "000123"}}}
        rows = {"테스트법": {"제7조": dict(OLD), "제2조": dict(OLD, version="연혁 · 2020.06.11 시행")}}
        original = copy.deepcopy(rows)
        with patch.object(Edition, "document", side_effect=TimeoutError("timeout")):
            changes, errors, counts = sync(laws, rows, lambda *_: NEW)
        self.assertEqual(rows, original)
        self.assertFalse(changes)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sum(counts.values()), 1)

    def test_effective_slice_comes_from_current_permalink(self):
        landing = html.fromstring('<form><input id="lsId" value="000123"><input id="lsNm" value="테스트법"></form>')
        wrapper = html.fromstring('<iframe id="lawService" src="/LSW/lsInfoP.do?lsiSeq=321&amp;efYd=20240101"></iframe>')
        with patch("sync.fetch", side_effect=[landing, wrapper]):
            current = current_edition("테스트법", {"lawId": "000123"})
        self.assertEqual((current.seq, current.effective), ("321", "20240101"))

    def test_temporary_failures_request_one_fresh_attempt(self):
        issues = []
        for error in (TimeoutError(), ConnectionResetError(), IncompleteRead(b""),
                      URLError(socket.gaierror(socket.EAI_AGAIN, "DNS retry")),
                      HTTPError("", 429, "Rate limited", {}, None),
                      HTTPError("", 503, "Service unavailable", {}, None),
                      IncompleteSourceResponse("missing current edition frame")):
            with self.subTest(error=error):
                self.assertTrue(temporary_source_error(error))
                issues.append(SourceIssue(str(error), True))
        self.assertEqual(result_code(issues), 75)
        self.assertEqual(result_code([]), 0)
        for error in (HTTPError("", 404, "Not found", {}, None), ValueError("ID mismatch"),
                      URLError(ssl.SSLCertVerificationError("invalid certificate"))):
            with self.subTest(error=error):
                self.assertFalse(temporary_source_error(error))
                self.assertEqual(result_code(issues + [SourceIssue(str(error), False)]), 1)

    def test_request_retry_is_bounded_and_skips_permanent_errors(self):
        for status, expected in ((503, 3), (404, 1)):
            with self.subTest(status=status), patch("sync.time.sleep") as sleep:
                error = HTTPError("", status, "HTTP failure", {}, None)
                with patch("sync.urlopen", side_effect=error) as get:
                    with self.assertRaises(HTTPError):
                        fetch("https://www.law.go.kr/LSW/test")
                self.assertEqual(get.call_count, expected)
                self.assertEqual(sleep.call_count, expected-1)

    def test_stopped_transient_batch_preserves_inputs_and_retry_reason(self):
        laws = {"laws": {f"법{i}": {"lawId": str(i)} for i in range(7)}}
        rows = {title: {"제7조": dict(OLD)} for title in laws["laws"]}
        original = copy.deepcopy(rows)
        def unavailable(*_):
            raise TimeoutError("source timed out")

        with patch("builtins.print"):
            changes, errors, _ = sync(laws, rows, unavailable)
        self.assertFalse(changes)
        self.assertEqual(rows, original)
        self.assertEqual(len(errors), 7)
        self.assertIn("1개 문서 대조 중단", errors[-1].message)
        self.assertEqual(result_code(errors), 75)

    def test_admin_identity_bootstrap_changes_once(self):
        laws = {"laws": {"규칙": {"adminSeq": "123"}}}
        rows = {"규칙": {"제1조": {"text": "본문", "version": "2025.01.01 조회",
                                "url": "https://www.law.go.kr/LSW/admRulInfoP.do?admRulSeq=123"}}}
        edition = Edition(True, "456", "123", "20240101", "규칙")
        changes, errors, _ = sync(laws, rows, lambda *_: edition)
        self.assertFalse(errors)
        self.assertEqual(len(changes), 1)
        self.assertEqual(laws["laws"]["규칙"]["adminId"], "456")
        self.assertFalse(sync(laws, rows, lambda *_: edition)[0])

    def test_article_link_has_specific_effective_slice(self):
        self.assertIn("efYd=20260101#J7:3", NEW.link("제7조의3제1항"))

if __name__ == "__main__":
    unittest.main()
