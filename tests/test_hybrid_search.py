import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import koica_search as ks
import semantic_search


def make_article(
    *,
    source: str = "인사규정",
    category: str = "규정",
    article_no: int = 1,
    article_sub: int = 0,
    title: str = "테스트",
    body: str = "테스트 본문",
) -> ks.Article:
    article = f"제{article_no}조" + (f"의{article_sub}" if article_sub else "")
    return ks.Article(
        category=category,
        source=source,
        revision="2026.08.02.",
        file="data/extracted/test.md",
        chapter="제1장 테스트",
        article=article,
        article_no=article_no,
        article_sub=article_sub,
        article_title=title,
        body=body,
    )


def make_attachment(
    *,
    source: str = "인사규정",
    category: str = "규정",
    number: str = "1",
    title: str = "테스트 서식",
    body: str = "테스트 별지 본문",
    deleted: bool = False,
) -> ks.Attachment:
    return ks.Attachment(
        category=category,
        source=source,
        revision="2026.08.02.",
        file="data/extracted/test.md",
        kind="별지",
        label=f"[별지 {number}]",
        number=number,
        title=title,
        body=body,
        deleted=deleted,
    )


class ReciprocalRankFusionTests(unittest.TestCase):
    def test_default_fusion_keeps_both_single_channel_candidates(self):
        keyword_only = ("article", 0)
        semantic_only = ("article", 1)

        fused = ks._rrf_fuse([keyword_only], [semantic_only])

        self.assertEqual({key for key, _score in fused}, {keyword_only, semantic_only})
        self.assertEqual(fused[0][1], fused[1][1])

    def test_equal_weights_are_symmetric_and_cross_channel_hits_are_deduplicated(self):
        first = ("article", 0)
        second = ("article", 1)

        fused = ks._rrf_fuse(
            [first, second],
            [second, first],
            k=60,
            semantic_weight=1.0,
        )

        self.assertEqual([key for key, _score in fused], [first, second])
        self.assertEqual(len(fused), 2)
        expected = (1.0 / 61.0) + (1.0 / 62.0)
        self.assertAlmostEqual(fused[0][1], expected)
        self.assertAlmostEqual(fused[1][1], expected)

    def test_candidate_found_by_both_channels_is_returned_once_with_both_scores(self):
        shared = ("article", 0)
        keyword_only = ("article", 1)

        fused = ks._rrf_fuse(
            [shared, keyword_only],
            [shared],
            k=60,
            semantic_weight=1.0,
        )

        self.assertEqual([key for key, _score in fused].count(shared), 1)
        self.assertEqual(fused[0][0], shared)
        self.assertAlmostEqual(fused[0][1], 2.0 / 61.0)

    def test_both_channel_heads_are_preserved_when_consensus_hits_fill_window(self):
        keyword_head = ("article", 0)
        semantic_head = ("article", 1)
        shared = [("article", index) for index in range(2, 11)]
        keyword_keys = [keyword_head, *shared]
        semantic_keys = [semantic_head, *shared]
        fused = ks._rrf_fuse(keyword_keys, semantic_keys)

        selected = ks._preserve_channel_heads(
            fused,
            keyword_keys,
            semantic_keys,
            limit=10,
        )

        self.assertEqual(len(selected), 10)
        self.assertIn(keyword_head, [key for key, _score in selected])
        self.assertIn(semantic_head, [key for key, _score in selected])


class HybridSearchContractTests(unittest.TestCase):
    def test_legacy_positional_call_falls_back_to_the_keyword_ranking(self):
        article = make_article(
            article_no=45,
            article_sub=3,
            title="육아휴직",
            body="직원이 육아휴직을 신청하는 경우 이를 허용하여야 한다.",
        )

        with (
            patch.object(ks, "load_index", return_value=[article]),
            patch.object(ks, "load_attachments", return_value=[]),
            patch.object(
                semantic_search,
                "semantic_rank",
                side_effect=semantic_search.SemanticUnavailable("offline"),
            ) as semantic_rank,
            patch.object(ks, "_SEMANTIC_WARNING_EMITTED", False),
            patch("sys.stderr", new=io.StringIO()),
        ):
            # Six positional arguments were the complete public signature before
            # hybrid search was added.  They must retain their original meaning.
            fallback = ks.search("육아휴직", None, None, 1, False, False)
            keyword = ks.search(
                "육아휴직", None, None, 1, False, False, mode="keyword"
            )

        semantic_rank.assert_called_once()
        self.assertEqual(fallback[0]["citation"], keyword[0]["citation"])
        self.assertEqual(fallback[0]["keyword_score"], keyword[0]["keyword_score"])
        self.assertEqual(fallback[0]["search_mode"], "keyword_fallback")
        self.assertEqual(fallback[0]["matched_by"], "keyword")
        self.assertIsNone(fallback[0]["semantic_score"])

    def test_keyword_mode_never_calls_the_semantic_backend(self):
        article = make_article(title="육아휴직", body="육아휴직 신청 절차")
        with (
            patch.object(ks, "load_index", return_value=[article]),
            patch.object(ks, "load_attachments", return_value=[]),
            patch.object(semantic_search, "semantic_rank") as semantic_rank,
        ):
            results = ks.search("육아휴직", mode="keyword")

        semantic_rank.assert_not_called()
        self.assertEqual(results[0]["search_mode"], "keyword")

    def test_semantic_mode_skips_keyword_tokenization_and_corpus_scan(self):
        article = make_article(title="육아휴직", body="육아휴직 신청 절차")
        with (
            patch.object(ks, "load_index", return_value=[article]),
            patch.object(ks, "load_attachments", return_value=[]),
            patch.object(ks, "tokenize", side_effect=AssertionError("keyword scan")),
            patch.object(semantic_search, "semantic_rank", return_value=[]),
        ):
            results = ks.search("아이를 돌보려고 쉰다", mode="semantic")

        self.assertEqual(results, [])

    def test_semantic_only_candidate_handles_wording_mismatch(self):
        target = make_article(
            article_no=45,
            article_sub=3,
            title="육아휴직",
            body="만 8세 이하의 자녀를 양육하기 위한 휴직을 허용한다.",
        )
        query = "아이를 돌보느라 잠시 쉬고 싶다"
        self.assertEqual(ks.score_article(target, ks.tokenize(query))[0], 0.0)

        dense_hit = {
            "kind": "article",
            "item_index": 0,
            "score": 0.91,
            "start": 0,
            "end": len(target.body),
        }
        with (
            patch.object(ks, "load_index", return_value=[target]),
            patch.object(ks, "load_attachments", return_value=[]),
            patch.object(
                semantic_search, "semantic_rank", return_value=[dense_hit]
            ) as semantic_rank,
        ):
            results = ks.search(query, mode="hybrid")

        semantic_rank.assert_called_once()
        self.assertEqual(results[0]["citation"], "인사규정 제45조의3")
        self.assertEqual(results[0]["matched_by"], "semantic")
        self.assertIsNone(results[0]["keyword_score"])
        self.assertEqual(results[0]["semantic_score"], 0.91)

    def test_category_source_and_attachment_filters_are_passed_to_semantic_rank(self):
        articles = [
            make_article(source="인사규정", category="규정", article_no=1),
            make_article(source="복무규정", category="규정", article_no=2),
            make_article(source="인사관리지침", category="지침", article_no=3),
        ]
        attachments = [
            make_attachment(source="인사규정", category="규정", number="1"),
            make_attachment(source="복무규정", category="규정", number="2"),
            make_attachment(
                source="인사규정", category="규정", number="3", deleted=True
            ),
        ]

        with (
            patch.object(ks, "load_index", return_value=articles),
            patch.object(ks, "load_attachments", return_value=attachments),
            patch.object(
                semantic_search, "semantic_rank", return_value=[]
            ) as semantic_rank,
        ):
            ks.search(
                "휴직 문의",
                category="규정",
                source="인사",
                include_attachments=True,
                mode="semantic",
            )

        call = semantic_rank.call_args.kwargs
        self.assertEqual(call["article_allowed"], [True, False, False])
        self.assertEqual(call["attachment_allowed"], [True, False, False])

        with (
            patch.object(ks, "load_index", return_value=articles),
            patch.object(ks, "load_attachments", return_value=attachments),
            patch.object(
                semantic_search, "semantic_rank", return_value=[]
            ) as semantic_rank,
        ):
            ks.search(
                "휴직 문의",
                category="규정",
                source="인사",
                include_attachments=False,
                mode="semantic",
            )

        self.assertEqual(
            semantic_rank.call_args.kwargs["attachment_allowed"],
            [False, False, False],
        )

    def test_stopword_only_query_still_reaches_semantic_search(self):
        article = make_article(title="휴직", body="자녀 양육을 위한 휴직")
        query = "다음 중 옳은 것은"
        self.assertEqual(ks.tokenize(query), [])
        dense_hit = {
            "kind": "article",
            "item_index": 0,
            "score": 0.72,
            "start": 0,
            "end": len(article.body),
        }

        with (
            patch.object(ks, "load_index", return_value=[article]),
            patch.object(ks, "load_attachments", return_value=[]),
            patch.object(
                semantic_search, "semantic_rank", return_value=[dense_hit]
            ) as semantic_rank,
        ):
            results = ks.search(query, mode="hybrid")

        semantic_rank.assert_called_once()
        self.assertEqual(semantic_rank.call_args.args[0], query)
        self.assertEqual(results[0]["matched_by"], "semantic")

    def test_invalid_mode_is_rejected_before_loading_the_corpus(self):
        with patch.object(ks, "load_index") as load_index:
            with self.assertRaisesRegex(ValueError, "mode는 다음 중 하나"):
                ks.search("육아휴직", mode="vector")

        load_index.assert_not_called()


class SemanticArtifactTests(unittest.TestCase):
    def test_recent_operator_marker_pauses_dense_search_and_stale_one_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "semantic-search.paused"
            marker.touch()
            modified_at = marker.stat().st_mtime
            env = {
                "KOICA_SEMANTIC_PAUSE_FILE": str(marker),
                "KOICA_SEMANTIC_PAUSE_MAX_AGE_SEC": "60",
            }

            with patch.dict(os.environ, env, clear=False):
                self.assertTrue(semantic_search.runtime_paused(now=modified_at + 30))
                self.assertFalse(
                    semantic_search.runtime_paused(now=modified_at + 61)
                )

    def test_pause_marker_rejects_dense_search_before_model_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "semantic-search.paused"
            marker.touch()
            with (
                patch.dict(
                    os.environ,
                    {"KOICA_SEMANTIC_PAUSE_FILE": str(marker)},
                    clear=False,
                ),
                patch.object(semantic_search, "get_encoder") as get_encoder,
            ):
                with self.assertRaisesRegex(
                    semantic_search.SemanticUnavailable, "동기화 중"
                ):
                    semantic_search.semantic_rank(
                        "육아휴직",
                        article_allowed=[],
                        attachment_allowed=[],
                    )

            get_encoder.assert_not_called()

    def test_colloquial_query_aliases_do_not_change_unrelated_queries(self):
        expanded = semantic_search.expand_query(
            "잘못 없이 납품이 늦어져도 벌금을 내나"
        )

        self.assertIn("책임 없는 사유", expanded)
        self.assertIn("계약 의무 지체", expanded)
        self.assertIn("지체상금", expanded)
        self.assertEqual(
            semantic_search.expand_query("인사위원회 구성"),
            "인사위원회 구성",
        )
        self.assertEqual(
            semantic_search.expand_query("규정을 위반하면 벌금을 내나"),
            "규정을 위반하면 벌금을 내나",
        )

    def test_character_chunks_overlap_and_include_the_tail(self):
        text = "0123456789XYZ"

        chunks = list(
            semantic_search.iter_text_chunks(text, chunk_size=6, overlap=2)
        )

        self.assertEqual(
            chunks,
            [
                (0, 6, "012345"),
                (4, 10, "456789"),
                (8, 13, "89XYZ"),
            ],
        )
        self.assertEqual(chunks[-1][1], len(text))
        self.assertEqual(chunks[-2][2][-2:], chunks[-1][2][:2])

    def test_stale_keyword_index_fingerprint_rejects_semantic_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_path = root / "index.json"
            vectors_path = root / "semantic_vectors.npy"
            meta_path = root / "semantic_meta.json"
            index_path.write_text('{"version":2,"articles":[]}', encoding="utf-8")
            np.save(
                vectors_path,
                np.zeros((1, semantic_search.MODEL_DIMENSION), dtype=np.float32),
            )
            meta_path.write_text(
                json.dumps(
                    {
                        "schema_version": semantic_search.SEMANTIC_SCHEMA_VERSION,
                        "model_revision": semantic_search.MODEL_REVISION,
                        "index_sha256": "stale-fingerprint",
                        "vector_count": 1,
                        "chunks": [
                            {
                                "kind": "article",
                                "item_index": 0,
                                "start": 0,
                                "end": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                semantic_search.SemanticUnavailable,
                "키워드 인덱스와 의미 인덱스가 서로 다름",
            ):
                semantic_search.SemanticIndex(
                    index_path=index_path,
                    vectors_path=vectors_path,
                    meta_path=meta_path,
                )


if __name__ == "__main__":
    unittest.main()
