"""공개 원격 서버(server_http.py)의 자원 고갈 방어 장치 회귀 테스트.

원격 MCP는 인증이 없고 512MB 머신 1대에서 동작하며, 동기 도구는 서버의 이벤트
루프에서 직접 실행된다. 따라서 (1) 요청 하나가 코퍼스 전체를 반복 스캔하지 못하게
하는 verify_citation 상한과 (2) initialize 반복으로 세션이 쌓이지 않게 하는 stateless
설정은 성능 튜닝이 아니라 가용성 방어선이다. 되돌아가면 서비스가 멈춘다.
"""

import unittest
from unittest.mock import patch

import koica_search as ks


def make_article(article_no: int, *, source: str = "인사규정") -> ks.Article:
    return ks.Article(
        category="규정",
        source=source,
        revision="2026.08.02.",
        file="data/extracted/test.md",
        chapter="제1장 테스트",
        article=f"제{article_no}조",
        article_no=article_no,
        article_sub=0,
        article_title="테스트",
        body="테스트 본문",
    )


class VerifyCitationLimitTests(unittest.TestCase):
    """상한 안에서는 동작이 그대로, 넘어서면 잘라내고 사실을 알린다."""

    def setUp(self):
        self.articles = [make_article(n) for n in range(1, 6)]

    def _verify(self, text: str) -> list[dict]:
        with patch.object(ks, "load_index", return_value=self.articles):
            return ks.verify_citation(text)

    def test_ordinary_input_is_unaffected_by_the_limits(self):
        results = self._verify("인사규정 제3조에 따라 처리한다.")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ok")
        self.assertNotIn("truncated", [r["status"] for r in results])

    def test_citation_count_at_the_limit_is_fully_verified(self):
        text = " ".join(
            f"인사규정 제{(i % 5) + 1}조" for i in range(ks.VERIFY_MAX_CITATIONS)
        )
        results = self._verify(text)
        self.assertEqual(len(results), ks.VERIFY_MAX_CITATIONS)
        self.assertNotIn("truncated", [r["status"] for r in results])

    def test_excess_citations_are_capped_and_reported(self):
        excess = 25
        text = " ".join(
            f"인사규정 제{(i % 5) + 1}조"
            for i in range(ks.VERIFY_MAX_CITATIONS + excess)
        )
        results = self._verify(text)

        notice = results[-1]
        self.assertEqual(notice["status"], "truncated")
        self.assertEqual(notice["verified"], ks.VERIFY_MAX_CITATIONS)
        self.assertEqual(notice["skipped"], excess)
        # 알림 1건을 뺀 나머지는 모두 실제 검증 결과여야 한다.
        self.assertEqual(len(results) - 1, ks.VERIFY_MAX_CITATIONS)

    def test_oversized_text_is_truncated_before_scanning(self):
        # 상한을 넘긴 뒤의 인용은 아예 읽히지 않는다(잘린 구간의 규정명도 미검출).
        filler = "가" * ks.VERIFY_MAX_TEXT_CHARS
        results = self._verify(filler + " 인사규정 제3조")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "truncated")
        self.assertEqual(results[0]["verified"], 0)
        self.assertIn(str(ks.VERIFY_MAX_TEXT_CHARS), results[0]["message"])

    def test_lookup_does_not_rescan_the_corpus_per_citation(self):
        """인용 수를 늘려도 인덱스 적재는 1회여야 한다(선형 재스캔 방지)."""
        text = " ".join(f"인사규정 제{(i % 5) + 1}조" for i in range(50))
        with patch.object(ks, "load_index", return_value=self.articles) as load_index:
            ks.verify_citation(text)
        self.assertEqual(load_index.call_count, 1)


class RemoteTransportTests(unittest.TestCase):
    def test_remote_server_is_stateless(self):
        """상태 유지 모드는 회수되지 않는 세션을 쌓아 메모리를 고갈시킨다."""
        import server_http

        self.assertTrue(server_http.mcp.settings.stateless_http)

    def test_remote_server_exposes_only_read_only_tools(self):
        import asyncio

        import server_http

        names = {tool.name for tool in asyncio.run(server_http.mcp.list_tools())}
        self.assertEqual(names & {"update", "sync_from_alio", "find_questions"}, set())


if __name__ == "__main__":
    unittest.main()
