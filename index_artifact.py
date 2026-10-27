"""Package/verify CI search indexes without executing the embedding model.

Only the fixed artifact filenames are accepted. The manifest binds the complete
bundle to its source corpus, producer code, dependency lock and optional commit.
GitHub transfers it within the same workflow run; hashes detect stale/mixed files,
not a malicious producer with permission to replace both files and manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import koica_search as ks
import semantic_search as ss

ROOT = Path(__file__).resolve().parent
FILES = ("index.json", "semantic_vectors.npy", "semantic_meta.json")
PRODUCERS = (
    "koica_search.py", "semantic_search.py", "index_artifact.py",
    "requirements.lock.txt", "tests/evaluate_semantic_quality.py",
)


def inputs() -> dict[str, str]:
    result = {name: ss.sha256_file(ROOT / name) for name in PRODUCERS}
    result["corpus"] = ks.corpus_fingerprint()
    return result


def validate_index(directory: Path) -> None:
    lexical = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    if lexical.get("corpus_sha256") != ks.corpus_fingerprint():
        raise ValueError("인덱스와 현재 규정 원문이 다릅니다")
    articles = [ks.Article(**row) for row in lexical["articles"]]
    attachments = [ks.Attachment(**row) for row in lexical["attachments"]]
    dense = ss.SemanticIndex(
        index_path=directory / "index.json",
        vectors_path=directory / "semantic_vectors.npy",
        meta_path=directory / "semantic_meta.json",
    )
    metadata = json.loads((directory / "semantic_meta.json").read_text(encoding="utf-8"))
    expected = [chunk.metadata() for chunk in ss.make_chunks(articles, attachments)]
    if metadata["chunks"] != expected:
        raise ValueError("벡터와 현재 조문·별표 매핑이 다릅니다")
    del dense


def pack(source: Path, destination: Path, revision: str) -> None:
    validate_index(source)
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if (source / name).resolve() != (destination / name).resolve():
            shutil.copyfile(source / name, destination / name)
    manifest = {
        "schema_version": 1,
        "revision": revision,
        "inputs": inputs(),
        "files": {name: ss.sha256_file(destination / name) for name in FILES},
    }
    temporary = destination / ".manifest.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination / "manifest.json")
    verify(destination, revision)


def verify(directory: Path, revision: str = "") -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("지원하지 않는 artifact manifest")
    if revision and manifest.get("revision") != revision:
        raise ValueError("artifact 커밋 불일치")
    if manifest.get("inputs") != inputs():
        raise ValueError("artifact의 원문·코드·의존성이 현재 체크아웃과 다릅니다")
    if manifest.get("files") != {name: ss.sha256_file(directory / name) for name in FILES}:
        raise ValueError("artifact 파일 SHA-256 불일치")
    validate_index(directory)


def install(directory: Path, destination: Path, revision: str = "") -> None:
    # Nothing is copied until every checksum, input and mapping passes.
    verify(directory, revision)
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        temporary = destination / f".{name}.tmp"
        shutil.copyfile(directory / name, temporary)
        os.replace(temporary, destination / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pack", "verify", "install"))
    parser.add_argument("--directory", type=Path, default=ROOT / "docker-index")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--revision", default="")
    args = parser.parse_args()
    if args.command == "pack":
        pack(args.data_dir, args.directory, args.revision)
    elif args.command == "install":
        install(args.directory, args.data_dir, args.revision)
    else:
        verify(args.directory, args.revision)
    print(f"검색 인덱스 artifact {args.command} 완료: {args.directory}")


if __name__ == "__main__":
    main()
