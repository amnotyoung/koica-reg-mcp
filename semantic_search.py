"""Dense semantic indexing and retrieval for KOICA regulations.

The module deliberately keeps heavyweight dependencies lazy.  Commands that only
use keyword search can therefore still import :mod:`koica_search` without loading
NumPy, ONNX Runtime, or the embedding model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_INDEX_PATH = DATA_DIR / "index.json"
DEFAULT_VECTORS_PATH = DATA_DIR / "semantic_vectors.npy"
DEFAULT_META_PATH = DATA_DIR / "semantic_meta.json"
DEFAULT_PAUSE_MAX_AGE_SEC = 2 * 60 * 60

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_DIMENSION = 384
MODEL_FILENAME = "model_qint8_avx512_vnni.onnx"
TOKENIZER_FILENAME = "sentencepiece.bpe.model"
MODEL_FILES = {
    MODEL_FILENAME: (
        f"https://huggingface.co/{MODEL_NAME}/resolve/{MODEL_REVISION}/"
        "onnx/model_qint8_avx512_vnni.onnx",
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
    ),
    TOKENIZER_FILENAME: (
        f"https://huggingface.co/{MODEL_NAME}/resolve/{MODEL_REVISION}/"
        "sentencepiece.bpe.model",
        "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    ),
}

SEMANTIC_SCHEMA_VERSION = 1
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80
DEFAULT_BATCH_SIZE = 8

# 자연어와 규정 용어 사이의 간극이 특히 큰 KOICA 인사·복무·계약·정보공개
# 표현만 보강한다. 원문을 대체하지 않고 E5 질의 뒤에 관련 통제어를 붙이므로,
# 기존 키워드 채널의 점수와 순위는 전혀 바뀌지 않는다.
_QUERY_ALIAS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?:아이|자녀).{0,18}(?:돌보|키우|양육|쉬|휴직)"),
        "자녀 양육 육아휴직",
    ),
    (re.compile(r"주말"), "토요일 휴일 유급휴일 무급휴일"),
    (re.compile(r"(?:피.{0,8}(?:뽑|기부)|혈액.{0,8}기부|헌혈)"), "헌혈 공가"),
    (
        re.compile(r"(?:퇴사|그만두|퇴직).{0,18}(?:정산금|돈|금액|언제)"),
        "퇴직 퇴직금 지급 시기",
    ),
    (
        re.compile(r"(?:퇴사|그만두|퇴직).{0,18}(?:나이|몇\s*살|연령)"),
        "정년 퇴직 나이",
    ),
    (
        re.compile(r"(?:마감|납품|계약).{0,30}(?:늦|지연|지체|못\s*지)"),
        "계약 의무 지체",
    ),
    (
        re.compile(
            r"(?=.*(?:마감|납품|계약|업체|지연|지체|늦))"
            r"(?=.*(?:페널티|패널티|벌금|위약금))"
        ),
        "지체상금",
    ),
    (
        re.compile(
            r"(?=.*(?:마감|납품|계약|지연|지체|늦))"
            r"(?=.*(?:잘못|책임).{0,5}(?:없|아니))"
        ),
        "계약상대자 책임 없는 사유",
    ),
    (re.compile(r"(?:내부고발|공익제보|공익신고)"), "공익신고 공익신고자"),
    (
        re.compile(r"(?:정체|신분|인적사항).{0,15}(?:공개|알리|까발|누설)"),
        "인적사항 신분 비밀보장 누설 금지",
    ),
    (
        re.compile(r"(?:신고|제보).{0,18}(?:보복|불이익)"),
        "공익신고등을 이유로 임직원에게 불이익조치를 해서는 아니 된다 "
        "불이익조치 등의 금지",
    ),
    (
        re.compile(r"(?:따돌림|폭언|직장\s*내\s*괴롭힘|괴롭힘)"),
        "직장 내 괴롭힘 신고 사건 접수 예방 대응 담당자",
    ),
    (re.compile(r"(?:자료|정보).{0,18}(?:보여|공개|열람)"), "정보공개 청구"),
    (
        re.compile(r"(?:정보|자료|공개).{0,24}(?:언제까지|기한|기간|며칠)"),
        "공개여부 결정 통지 기간",
    ),
    (
        re.compile(
            r"(?:부모|가족|배우자).{0,24}"
            r"(?:아프|아파|아픈|간병|돌보|보살피|보살펴)"
        ),
        "가족돌봄 휴직 가족돌봄 등을 위한 지원",
    ),
    (
        re.compile(r"(?:정보|자료|공개).{0,20}(?:거절|비공개)"),
        "정보공개 비공개 결정",
    ),
    (
        re.compile(r"(?:거절|비공개).{0,20}(?:다시|따지|불복|이의)"),
        "불복 이의신청",
    ),
    (
        re.compile(r"(?:집에서|재택|원격).{0,15}(?:컴퓨터|일|근무)"),
        "재택근무 원격근무 유연근무",
    ),
    (
        re.compile(
            r"(?:견적\s*없이|한\s*(?:곳|업체).{0,12}(?:맡|계약)|바로\s*맡)"
        ),
        "수의계약 조건",
    ),
)


class SemanticUnavailable(RuntimeError):
    """Raised when dense search cannot safely be used."""


def runtime_paused(now: float | None = None) -> bool:
    """Return whether an operator pause marker temporarily disables dense search.

    Fly's weekly ALIO conversion runs Node/kordoc on the same 512 MB Machine.  The
    workflow writes a marker to the persistent volume and restarts the Machine
    first, so requests use the existing keyword fallback instead of loading ONNX
    at the same time.  A stale marker expires automatically after two hours.
    """

    configured = os.environ.get("KOICA_SEMANTIC_PAUSE_FILE", "").strip()
    if not configured:
        return False
    marker = Path(configured)
    try:
        modified_at = marker.stat().st_mtime
    except FileNotFoundError:
        return False
    except OSError:
        # An operator deliberately configured a marker path.  If its state cannot
        # be inspected, fail closed to the low-memory keyword channel.
        return True

    raw_max_age = os.environ.get("KOICA_SEMANTIC_PAUSE_MAX_AGE_SEC", "")
    try:
        max_age = float(raw_max_age) if raw_max_age else DEFAULT_PAUSE_MAX_AGE_SEC
    except ValueError:
        max_age = DEFAULT_PAUSE_MAX_AGE_SEC
    max_age = max(1.0, max_age)
    return (time.time() if now is None else now) - modified_at <= max_age


def expand_query(query: str) -> str:
    """Append narrow KOICA-domain aliases for colloquial Korean expressions."""

    additions: list[str] = []
    for pattern, terms in _QUERY_ALIAS_RULES:
        if pattern.search(query):
            additions.append(terms)
    if not additions:
        return query
    # A single query can activate complementary rules (for example late delivery,
    # no fault, and a penalty).  Preserve rule order while removing repeats.
    unique = list(dict.fromkeys(additions))
    return f"{query}\n관련 규정 용어: {' '.join(unique)}"


def default_model_dir() -> Path:
    configured = os.environ.get("KOICA_SEMANTIC_MODEL_DIR")
    if configured:
        return Path(configured).expanduser()
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "koica-reg-mcp" / "multilingual-e5-small"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_model_file(path: Path, expected_sha256: str) -> bool:
    return path.is_file() and sha256_file(path) == expected_sha256


def download_model(model_dir: Path | str | None = None) -> Path:
    """Download the pinned, quantized E5 model and verify both file hashes."""

    destination = Path(model_dir) if model_dir is not None else default_model_dir()
    destination.mkdir(parents=True, exist_ok=True)

    for filename, (url, expected_sha256) in MODEL_FILES.items():
        target = destination / filename
        if _valid_model_file(target, expected_sha256):
            continue

        temporary = target.with_name(f".{target.name}.part")
        temporary.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "koica-reg-mcp-semantic-index/1"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                with temporary.open("wb") as output:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(block)
            actual_sha256 = sha256_file(temporary)
            if actual_sha256 != expected_sha256:
                raise SemanticUnavailable(
                    f"{filename} 해시 불일치: {actual_sha256}"
                )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    return destination


def _require_model_dir(model_dir: Path | str | None = None) -> Path:
    directory = Path(model_dir) if model_dir is not None else default_model_dir()
    missing = [
        filename for filename in MODEL_FILES if not (directory / filename).is_file()
    ]
    if missing:
        raise SemanticUnavailable(
            "의미 검색 모델 파일이 없습니다: " + ", ".join(missing)
        )
    corrupt = [
        filename
        for filename, (_, expected_sha256) in MODEL_FILES.items()
        if not _valid_model_file(directory / filename, expected_sha256)
    ]
    if corrupt:
        raise SemanticUnavailable(
            "의미 검색 모델 파일의 SHA-256이 맞지 않습니다: " + ", ".join(corrupt)
        )
    return directory


class DenseEncoder:
    """Small direct ONNX wrapper for ``multilingual-e5-small``."""

    def __init__(self, model_dir: Path | str | None = None) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            import sentencepiece as spm
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise SemanticUnavailable(
                "의미 검색 의존성(numpy, onnxruntime, sentencepiece)이 없습니다."
            ) from exc

        directory = _require_model_dir(model_dir)
        options = ort.SessionOptions()
        thread_count = max(1, int(os.environ.get("KOICA_SEMANTIC_THREADS", "1")))
        options.intra_op_num_threads = thread_count
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        try:
            self._session = ort.InferenceSession(
                str(directory / MODEL_FILENAME),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._tokenizer = spm.SentencePieceProcessor(
                model_file=str(directory / TOKENIZER_FILENAME)
            )
        except Exception as exc:
            raise SemanticUnavailable(
                f"의미 검색 모델을 열 수 없습니다: {exc}"
            ) from exc

        self._np = np
        self._lock = threading.Lock()

    def _token_ids(self, text: str) -> list[int]:
        # XLM-R token ids differ from raw SentencePiece ids: <s>=0, <pad>=1,
        # </s>=2, <unk>=3, and every normal piece is shifted by one.
        pieces = self._tokenizer.encode(text, out_type=int)[:510]
        inner = [3 if piece == 0 else piece + 1 for piece in pieces]
        return [0, *inner, 2]

    def _embed(self, texts: Sequence[str], prefix: str) -> Any:
        np = self._np
        if not texts:
            return np.empty((0, MODEL_DIMENSION), dtype=np.float32)

        encoded = [self._token_ids(f"{prefix}: {text}") for text in texts]
        width = max(len(row) for row in encoded)
        input_ids = np.full((len(encoded), width), 1, dtype=np.int64)
        attention_mask = np.zeros((len(encoded), width), dtype=np.int64)
        for row_number, token_ids in enumerate(encoded):
            input_ids[row_number, : len(token_ids)] = token_ids
            attention_mask[row_number, : len(token_ids)] = 1
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        inputs: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        input_names = {item.name for item in self._session.get_inputs()}
        if "token_type_ids" in input_names:
            inputs["token_type_ids"] = token_type_ids

        with self._lock:
            hidden_state = self._session.run(None, inputs)[0]
        mask = attention_mask[..., None].astype(np.float32)
        pooled = (hidden_state * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1.0)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.maximum(norms, 1e-12)).astype(np.float32)

    def embed_queries(self, texts: Sequence[str]) -> Any:
        return self._embed(texts, "query")

    def embed_passages(self, texts: Sequence[str]) -> Any:
        return self._embed(texts, "passage")


_ENCODER: DenseEncoder | None = None
_ENCODER_KEY: str | None = None
_ENCODER_LOCK = threading.Lock()


def get_encoder(model_dir: Path | str | None = None) -> DenseEncoder:
    global _ENCODER, _ENCODER_KEY
    key = (
        str(Path(model_dir).resolve())
        if model_dir is not None
        else str(default_model_dir())
    )
    with _ENCODER_LOCK:
        if _ENCODER is None or _ENCODER_KEY != key:
            _ENCODER = DenseEncoder(model_dir)
            _ENCODER_KEY = key
        return _ENCODER


def iter_text_chunks(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> Iterable[tuple[int, int, str]]:
    """Yield deterministic overlapping character chunks, including the tail."""

    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError(
            "chunk_size는 양수이고 overlap은 0 이상 chunk_size 미만이어야 합니다."
        )
    body = text or ""
    if not body:
        yield 0, 0, ""
        return

    start = 0
    while start < len(body):
        end = min(start + chunk_size, len(body))
        yield start, end, body[start:end]
        if end == len(body):
            break
        start = end - overlap


@dataclass(frozen=True)
class _Chunk:
    kind: str
    item_index: int
    start: int
    end: int
    text: str

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item_index": self.item_index,
            "start": self.start,
            "end": self.end,
        }


def _article_header(article: Any) -> str:
    fields = [
        getattr(article, "source", ""),
        getattr(article, "chapter", ""),
        getattr(article, "article", ""),
        getattr(article, "article_title", ""),
    ]
    return " · ".join(str(value) for value in fields if value)


def _attachment_header(attachment: Any) -> str:
    fields = [
        getattr(attachment, "source", ""),
        getattr(attachment, "kind", ""),
        getattr(attachment, "label", ""),
        getattr(attachment, "title", ""),
    ]
    return " · ".join(str(value) for value in fields if value)


def make_chunks(articles: Sequence[Any], attachments: Sequence[Any]) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    for item_index, article in enumerate(articles):
        header = _article_header(article)
        for start, end, body in iter_text_chunks(getattr(article, "body", "")):
            chunks.append(_Chunk("article", item_index, start, end, f"{header}\n{body}"))

    for item_index, attachment in enumerate(attachments):
        if getattr(attachment, "deleted", False):
            continue
        header = _attachment_header(attachment)
        for start, end, body in iter_text_chunks(getattr(attachment, "body", "")):
            chunks.append(_Chunk("attachment", item_index, start, end, f"{header}\n{body}"))
    return chunks


def invalidate_artifacts(
    vectors_path: Path | str = DEFAULT_VECTORS_PATH,
    meta_path: Path | str = DEFAULT_META_PATH,
) -> None:
    Path(meta_path).unlink(missing_ok=True)
    Path(vectors_path).unlink(missing_ok=True)
    reset_caches()


def build_semantic_index(
    articles: Sequence[Any],
    attachments: Sequence[Any],
    *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    vectors_path: Path | str = DEFAULT_VECTORS_PATH,
    meta_path: Path | str = DEFAULT_META_PATH,
    model_dir: Path | str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    download: bool = True,
) -> dict[str, Any]:
    """Encode every searchable chunk and atomically replace dense artifacts."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise SemanticUnavailable("의미 검색 의존성 numpy가 없습니다.") from exc

    index_file = Path(index_path)
    vectors_file = Path(vectors_path)
    meta_file = Path(meta_path)
    if not index_file.is_file():
        raise SemanticUnavailable(f"키워드 인덱스가 없습니다: {index_file}")
    if batch_size <= 0:
        raise ValueError("batch_size는 양수여야 합니다.")

    if download:
        directory = download_model(model_dir)
    else:
        directory = _require_model_dir(model_dir)
    encoder = get_encoder(directory)
    chunks = make_chunks(articles, attachments)
    if not chunks:
        raise SemanticUnavailable("임베딩할 검색 문서가 없습니다.")

    vectors_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_vectors = vectors_file.with_name(f".{vectors_file.name}.tmp")
    temporary_meta = meta_file.with_name(f".{meta_file.name}.tmp")
    temporary_vectors.unlink(missing_ok=True)
    temporary_meta.unlink(missing_ok=True)

    try:
        vectors = np.lib.format.open_memmap(
            temporary_vectors,
            mode="w+",
            dtype=np.float32,
            shape=(len(chunks), MODEL_DIMENSION),
        )
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors[start : start + len(batch)] = encoder.embed_passages(
                [chunk.text for chunk in batch]
            )
            completed = start + len(batch)
            if completed == len(chunks) or completed % 512 < batch_size:
                print(
                    f"의미 인덱스 임베딩: {completed}/{len(chunks)} 청크",
                    file=sys.stderr,
                    flush=True,
                )
        vectors.flush()
        del vectors

        metadata: dict[str, Any] = {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "model": MODEL_NAME,
            "model_revision": MODEL_REVISION,
            "dimension": MODEL_DIMENSION,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "index_sha256": sha256_file(index_file),
            "vector_count": len(chunks),
            "chunks": [chunk.metadata() for chunk in chunks],
        }
        temporary_meta.write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary_vectors, vectors_file)
        os.replace(temporary_meta, meta_file)
        reset_caches()
        return metadata
    except Exception:
        temporary_vectors.unlink(missing_ok=True)
        temporary_meta.unlink(missing_ok=True)
        # Never leave old dense data looking current after the lexical index changed.
        meta_file.unlink(missing_ok=True)
        reset_caches()
        raise


class SemanticIndex:
    def __init__(
        self,
        *,
        index_path: Path | str = DEFAULT_INDEX_PATH,
        vectors_path: Path | str = DEFAULT_VECTORS_PATH,
        meta_path: Path | str = DEFAULT_META_PATH,
    ) -> None:
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise SemanticUnavailable("의미 검색 의존성 numpy가 없습니다.") from exc

        self._np = np
        index_file = Path(index_path)
        vectors_file = Path(vectors_path)
        meta_file = Path(meta_path)
        if not vectors_file.is_file() or not meta_file.is_file():
            raise SemanticUnavailable("의미 검색 인덱스가 아직 생성되지 않았습니다.")

        try:
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))
            if metadata.get("schema_version") != SEMANTIC_SCHEMA_VERSION:
                raise ValueError("지원하지 않는 스키마")
            if metadata.get("model_revision") != MODEL_REVISION:
                raise ValueError("모델 리비전 불일치")
            if metadata.get("index_sha256") != sha256_file(index_file):
                raise ValueError("키워드 인덱스와 의미 인덱스가 서로 다름")

            chunks = metadata["chunks"]
            vectors = np.load(vectors_file, mmap_mode="r")
            expected_shape = (len(chunks), MODEL_DIMENSION)
            if (
                vectors.shape != expected_shape
                or metadata.get("vector_count") != len(chunks)
            ):
                raise ValueError("벡터와 메타데이터 개수 불일치")

            self.vectors = vectors
            self.kinds = np.asarray([row["kind"] for row in chunks])
            self.item_indices = np.asarray(
                [int(row["item_index"]) for row in chunks], dtype=np.int32
            )
            self.starts = np.asarray(
                [int(row["start"]) for row in chunks], dtype=np.int32
            )
            self.ends = np.asarray([int(row["end"]) for row in chunks], dtype=np.int32)
            if not np.isin(self.kinds, ("article", "attachment")).all():
                raise ValueError("알 수 없는 청크 종류")
            if self.item_indices.size and int(self.item_indices.min()) < 0:
                raise ValueError("음수 문서 인덱스")
        except SemanticUnavailable:
            raise
        except Exception as exc:
            raise SemanticUnavailable(
                f"의미 검색 인덱스가 유효하지 않습니다: {exc}"
            ) from exc

    def rank(
        self,
        query_vector: Any,
        *,
        article_allowed: Sequence[bool],
        attachment_allowed: Sequence[bool],
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        np = self._np
        if limit <= 0:
            return []

        article_mask = np.asarray(article_allowed, dtype=bool)
        attachment_mask = np.asarray(attachment_allowed, dtype=bool)
        valid = np.zeros(len(self.item_indices), dtype=bool)
        article_rows = self.kinds == "article"
        attachment_rows = self.kinds == "attachment"

        article_ids = self.item_indices[article_rows]
        attachment_ids = self.item_indices[attachment_rows]
        if article_ids.size and int(article_ids.max()) >= len(article_mask):
            raise SemanticUnavailable(
                "의미 검색 조문 매핑이 현재 데이터와 맞지 않습니다."
            )
        if attachment_ids.size and int(attachment_ids.max()) >= len(attachment_mask):
            raise SemanticUnavailable(
                "의미 검색 별표 매핑이 현재 데이터와 맞지 않습니다."
            )
        valid[article_rows] = article_mask[article_ids]
        valid[attachment_rows] = attachment_mask[attachment_ids]
        valid_rows = np.flatnonzero(valid)
        if not valid_rows.size:
            return []

        scores = np.asarray(self.vectors @ query_vector, dtype=np.float32)
        order = valid_rows[np.argsort(-scores[valid_rows], kind="stable")]
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for row in order:
            kind = str(self.kinds[row])
            item_index = int(self.item_indices[row])
            key = (kind, item_index)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "kind": kind,
                    "item_index": item_index,
                    "score": float(scores[row]),
                    "start": int(self.starts[row]),
                    "end": int(self.ends[row]),
                }
            )
            if len(results) >= limit:
                break
        return results


_SEMANTIC_INDEX: SemanticIndex | None = None
_SEMANTIC_INDEX_KEY: tuple[str, str, str] | None = None
_INDEX_LOCK = threading.Lock()
_SEARCH_LOCK = threading.Lock()


def get_semantic_index(
    *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    vectors_path: Path | str = DEFAULT_VECTORS_PATH,
    meta_path: Path | str = DEFAULT_META_PATH,
) -> SemanticIndex:
    global _SEMANTIC_INDEX, _SEMANTIC_INDEX_KEY
    key = (str(Path(index_path)), str(Path(vectors_path)), str(Path(meta_path)))
    with _INDEX_LOCK:
        if _SEMANTIC_INDEX is None or _SEMANTIC_INDEX_KEY != key:
            _SEMANTIC_INDEX = SemanticIndex(
                index_path=index_path,
                vectors_path=vectors_path,
                meta_path=meta_path,
            )
            _SEMANTIC_INDEX_KEY = key
        return _SEMANTIC_INDEX


def semantic_rank(
    query: str,
    *,
    article_allowed: Sequence[bool],
    attachment_allowed: Sequence[bool],
    limit: int = 50,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    vectors_path: Path | str = DEFAULT_VECTORS_PATH,
    meta_path: Path | str = DEFAULT_META_PATH,
    model_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    if not query.strip() or limit <= 0:
        return []
    if runtime_paused():
        raise SemanticUnavailable(
            "운영 데이터 동기화 중이라 의미 검색을 잠시 쉬고 있습니다."
        )
    # The deployed Fly machine has 512MB RAM.  Serialize the complete dense path so
    # a future MCP/threadpool change cannot overlap ONNX workspaces and matrix reads.
    with _SEARCH_LOCK:
        # Also check under the search lock so a marker created while another dense
        # request was finishing cannot be missed by the next request.
        if runtime_paused():
            raise SemanticUnavailable(
                "운영 데이터 동기화 중이라 의미 검색을 잠시 쉬고 있습니다."
            )
        encoder = get_encoder(model_dir)
        dense_index = get_semantic_index(
            index_path=index_path,
            vectors_path=vectors_path,
            meta_path=meta_path,
        )
        query_vector = encoder.embed_queries([expand_query(query)])[0]
        return dense_index.rank(
            query_vector,
            article_allowed=article_allowed,
            attachment_allowed=attachment_allowed,
            limit=limit,
        )


def reset_caches() -> None:
    global _ENCODER, _ENCODER_KEY, _SEMANTIC_INDEX, _SEMANTIC_INDEX_KEY
    with _ENCODER_LOCK:
        _ENCODER = None
        _ENCODER_KEY = None
    with _INDEX_LOCK:
        _SEMANTIC_INDEX = None
        _SEMANTIC_INDEX_KEY = None


def _main() -> None:
    parser = argparse.ArgumentParser(description="KOICA 규정 의미 검색 모델 관리")
    subparsers = parser.add_subparsers(dest="command", required=True)
    download_parser = subparsers.add_parser(
        "download-model", help="고정 버전 E5 모델 다운로드"
    )
    download_parser.add_argument("--model-dir", type=Path, default=None)
    args = parser.parse_args()

    if args.command == "download-model":
        destination = download_model(args.model_dir)
        print(f"의미 검색 모델 준비 완료: {destination}")


if __name__ == "__main__":
    _main()
