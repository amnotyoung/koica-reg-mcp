"""Offline regression checks for the scheduled law review."""
import copy
import unittest
from unittest.mock import patch

from lxml import html

from sync import Edition, current_edition, source_text, sync, update_excerpt

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
