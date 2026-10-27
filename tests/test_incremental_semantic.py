import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

import semantic_search as ss
from test_hybrid_search import make_article, make_attachment


class CountingEncoder:
    def __init__(self):
        self.calls = []

    def embed_passages(self, texts):
        self.calls.extend(texts)
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            row = np.resize(np.frombuffer(digest, dtype=np.uint8), ss.MODEL_DIMENSION).astype(np.float32) + 1
            rows.append(row / np.linalg.norm(row))
        return np.asarray(rows, dtype=np.float32)


class IncrementalSemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.index = self.root / "index.json"
        self.vectors = self.root / "semantic_vectors.npy"
        self.meta = self.root / "semantic_meta.json"
        self.encoder = CountingEncoder()
        self.download = self.enterContext(patch.object(ss, "download_model", return_value=self.root))
        self.get_encoder = self.enterContext(patch.object(ss, "get_encoder", return_value=self.encoder))

    def build(self, articles, attachments=(), **kwargs):
        self.index.write_text(json.dumps({
            "articles": [asdict(row) for row in articles],
            "attachments": [asdict(row) for row in attachments],
        }), encoding="utf-8")
        return ss.build_semantic_index(
            articles, attachments, index_path=self.index,
            vectors_path=self.vectors, meta_path=self.meta, **kwargs,
        )

    def dense(self):
        return ss.SemanticIndex(index_path=self.index, vectors_path=self.vectors, meta_path=self.meta)

    def test_unchanged_build_does_not_load_or_download_model(self):
        rows = [make_article(body="A" * 900), make_article(article_no=2)]
        first = self.build(rows)
        before = self.vectors.read_bytes()
        self.download.reset_mock()
        self.get_encoder.reset_mock()
        second = self.build(rows)
        self.assertEqual(second["embedded_unique_chunks"], 0)
        self.assertEqual(second["reused_chunks"], first["vector_count"])
        self.assertEqual(self.vectors.read_bytes(), before)
        self.download.assert_not_called()
        self.get_encoder.assert_not_called()

    def test_edit_insert_delete_and_reorder_match_a_clean_build(self):
        a, b, c = [make_article(article_no=i, body=str(i) * 900) for i in range(1, 4)]
        attachment = make_attachment(body="별지 " * 250)
        self.build([a, b, c], [attachment])
        edited = replace(b, body=b.body[:450] + "변경" + b.body[452:])
        added = make_article(article_no=4, body="새 조문")
        rows = [c, edited, added]  # deletion and array positions changing together
        stats = self.build(rows, [replace(attachment, deleted=True)])
        incremental = np.load(self.vectors).copy()
        mapping = json.loads(self.meta.read_text())["chunks"]
        self.assertGreater(stats["reused_chunks"], 0)
        self.assertGreater(stats["embedded_unique_chunks"], 0)
        self.assertLess(stats["embedded_unique_chunks"], stats["vector_count"])
        self.assertGreater(stats["removed_unique_chunks"], 0)
        self.assertTrue(all(row["kind"] == "article" for row in mapping))
        self.build(rows, [replace(attachment, deleted=True)], reuse=False)
        np.testing.assert_array_equal(incremental, np.load(self.vectors))
        self.assertEqual(mapping, json.loads(self.meta.read_text())["chunks"])

    def test_reorder_and_revision_change_reuse_vectors_but_rebuild_mapping(self):
        a, b = make_article(article_no=1), make_article(article_no=2)
        self.build([a, b])
        before = np.load(self.vectors).copy()
        stats = self.build([replace(b, revision="2026.10.27."), a])
        self.assertEqual(stats["embedded_unique_chunks"], 0)
        np.testing.assert_array_equal(before[::-1], np.load(self.vectors))
        dense = self.dense()
        hits = dense.rank(before[0], article_allowed=[True, True], attachment_allowed=[], limit=1)
        self.assertEqual(hits[0]["item_index"], 1)

    def test_header_changes_are_part_of_the_embedding_input(self):
        a = make_article(body="동일 본문")
        self.build([a])
        stats = self.build([replace(a, article_title="바뀐 제목")])
        self.assertEqual(stats["embedded_unique_chunks"], 1)

    def test_batch_size_change_invalidates_the_cache(self):
        rows = [make_article(), make_article(article_no=2)]
        self.build(rows, batch_size=2)
        stats = self.build(rows, batch_size=1)
        self.assertEqual(stats["reused_chunks"], 0)
        self.assertEqual(stats["embedded_chunks"], 2)

    def test_duplicate_occurrences_keep_their_batch_specific_vectors(self):
        row = make_article()
        # Simulate the batch-dependent rounding of dynamic int8 quantization.
        encoded = np.eye(2, ss.MODEL_DIMENSION, dtype=np.float32)
        with patch.object(self.encoder, "embed_passages", return_value=encoded) as embed:
            stats = self.build([row, row])
            self.assertEqual(len(embed.call_args.args[0]), 2)
        self.assertEqual(stats["embedded_chunks"], 2)
        self.assertEqual(stats["embedded_unique_chunks"], 1)
        stats = self.build([row, row])
        self.assertEqual(stats["embedded_chunks"], 0)
        np.testing.assert_array_equal(np.load(self.vectors), encoded)
        stats = self.build([row, row, row])
        self.assertEqual(stats["embedded_chunks"], 0)
        np.testing.assert_array_equal(np.load(self.vectors)[2], encoded[1])

    def test_model_tokenizer_and_encoder_changes_invalidate_reuse(self):
        row = make_article()
        for change in (
            patch.object(ss, "MODEL_REVISION", "new-revision"),
            patch.dict(ss.MODEL_FILES, {ss.TOKENIZER_FILENAME: ("url", "changed-tokenizer")}),
            patch.object(ss, "embedding_signature", return_value="new-encoder"),
        ):
            with self.subTest(change=change):
                self.build([row], reuse=False)
                with change:
                    stats = self.build([row])
                    self.assertEqual(stats["embedded_unique_chunks"], 1)

    def test_corrupt_or_old_cache_is_rebuilt_and_runtime_rejects_corruption(self):
        row = make_article()
        self.build([row])
        with self.vectors.open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"\xff")
        with self.assertRaises(ss.SemanticUnavailable):
            self.dense()
        self.assertEqual(self.build([row])["embedded_unique_chunks"], 1)
        metadata = json.loads(self.meta.read_text())
        metadata["schema_version"] = 1
        self.meta.write_text(json.dumps(metadata))
        self.assertEqual(self.build([row])["embedded_unique_chunks"], 1)

    def test_failed_embedding_keeps_previous_cache_but_cannot_serve_stale_index(self):
        row = make_article()
        self.build([row])
        old_vectors, old_meta = self.vectors.read_bytes(), self.meta.read_bytes()
        with patch.object(self.encoder, "embed_passages", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.build([row, make_article(article_no=2)])
        self.assertEqual(self.vectors.read_bytes(), old_vectors)
        self.assertEqual(self.meta.read_bytes(), old_meta)
        with self.assertRaisesRegex(ss.SemanticUnavailable, "키워드 인덱스와 의미 인덱스"):
            self.dense()
        stats = self.build([row, make_article(article_no=2)])
        self.assertEqual(stats["embedded_unique_chunks"], 1)
        self.assertEqual(stats["reused_chunks"], 1)

    def test_valid_json_with_corrupted_chunk_hash_is_not_reused(self):
        row = make_article()
        self.build([row])
        metadata = json.loads(self.meta.read_text())
        metadata["chunks"][0]["text_sha256"] = "a" * 64
        self.meta.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ss.SemanticUnavailable, "메타데이터"):
            self.dense()
        self.assertEqual(self.build([row])["embedded_unique_chunks"], 1)

    def test_nonfinite_encoder_output_is_never_published(self):
        with patch.object(self.encoder, "embed_passages", return_value=np.full((1, ss.MODEL_DIMENSION), np.nan)):
            with self.assertRaisesRegex(ValueError, "정규화"):
                self.build([make_article()])
        self.assertFalse(self.meta.exists())
        self.assertFalse(self.vectors.exists())


if __name__ == "__main__":
    unittest.main()
