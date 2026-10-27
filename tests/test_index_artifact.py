import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import index_artifact as artifact
import koica_search as ks
import semantic_search as ss
from test_incremental_semantic import CountingEncoder


class IndexArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.sources = self.data / "extracted"
        self.sources.mkdir(parents=True)
        self.source = self.sources / "규정_test.md"
        self.source.write_text("# 테스트규정\n## 제1장 총칙\n### 제1조(목적)\n테스트 본문\n", encoding="utf-8")
        self.bundle = self.root / "bundle"
        self.enterContext(patch.object(ks, "ROOT", self.root))
        self.enterContext(patch.object(ks, "DATA_DIR", self.sources))
        self.enterContext(patch.object(ks, "INDEX_PATH", self.data / "index.json"))
        self.enterContext(patch.object(ks, "SEMANTIC_VECTORS_PATH", self.data / "semantic_vectors.npy"))
        self.enterContext(patch.object(ks, "SEMANTIC_META_PATH", self.data / "semantic_meta.json"))
        self.enterContext(patch.object(ss, "download_model", return_value=self.root))
        self.enterContext(patch.object(ss, "get_encoder", return_value=CountingEncoder()))
        ks.build_index(strict_semantic=True)
        artifact.pack(self.data, self.bundle, "commit-a")

    def test_verified_bundle_installs_without_loading_the_model(self):
        destination = self.root / "installed"
        with patch.object(ss, "get_encoder", side_effect=AssertionError("must not embed")):
            artifact.install(self.bundle, destination, "commit-a")
        for name in artifact.FILES:
            self.assertEqual((self.bundle / name).read_bytes(), (destination / name).read_bytes())

    def test_wrong_commit_source_or_producer_fails(self):
        with self.assertRaisesRegex(ValueError, "커밋"):
            artifact.verify(self.bundle, "commit-b")
        with patch.object(artifact, "inputs", return_value={"code": "different"}):
            with self.assertRaisesRegex(ValueError, "원문·코드·의존성"):
                artifact.verify(self.bundle)
        self.source.write_text(self.source.read_text() + "추가 문장", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "원문·코드·의존성"):
            artifact.verify(self.bundle)
        with self.assertRaisesRegex(ValueError, "규정 원문"):
            artifact.pack(self.data, self.bundle, "commit-b")

    def test_corrupt_or_missing_file_fails_before_installing_any_file(self):
        (self.bundle / "semantic_vectors.npy").write_bytes(b"corrupt")
        destination = self.root / "installed"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            artifact.install(self.bundle, destination)
        self.assertFalse(destination.exists())
        (self.bundle / "semantic_vectors.npy").unlink()
        with self.assertRaises(FileNotFoundError):
            artifact.verify(self.bundle)

    def test_wrong_row_mapping_is_rejected_even_with_updated_file_checksum(self):
        path = self.bundle / "semantic_meta.json"
        metadata = json.loads(path.read_text())
        metadata["chunks"][0]["item_index"] = 99
        metadata["metadata_sha256"] = ss.metadata_digest(metadata)
        path.write_text(json.dumps(metadata))
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][path.name] = ss.sha256_file(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "매핑"):
            artifact.verify(self.bundle)


if __name__ == "__main__":
    unittest.main()
