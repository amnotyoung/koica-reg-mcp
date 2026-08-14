"""KOICA 규정 조문 단위 검색 (MVP).

사용:
    python koica_search.py build
    python koica_search.py search "인사위원회"
    python koica_search.py search "징계 시효" --category 규정
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "extracted"
INDEX_PATH = ROOT / "data" / "index.json"
SEMANTIC_VECTORS_PATH = ROOT / "data" / "semantic_vectors.npy"
SEMANTIC_META_PATH = ROOT / "data" / "semantic_meta.json"

CATEGORY_AGGREGATE_FILES = {
    "law.md", "hr.md", "project.md", "volunteer.md",
    "partnership.md", "finance.md", "management.md",
}

# 별표·별지 헤더 — 라인 어디에 있든 매칭 (한 라인에 여러 개 등장 가능)
_ATTACHMENT_HEAD_RE = re.compile(
    r"\[\s*(?P<kind>별표|별지)\s+(?P<number>(?:제\s*)?[\d\-]+(?:호)?\s*(?:서식)?)\s*\]\s*"
)

# 제목 헤더: 규정명 + (선택) 개정 정보.
# 규정명 자체에 괄호가 있을 수 있어(예: "오다(ODA)전문가…기준"), 말미 괄호는
# 개정 정보 형태(개정/제정/호 포함)일 때만 revision으로 인식한다.
HEADER_RE = re.compile(r"^# (.+?)(?:\s*\(([^()]*(?:개정|제정|호)[^()]*)\))?\s*$")
# Format A (마크다운 정형): "## 제N장 …", "### 제N조(제목)" 또는 "## 제N조(제목)"
CHAPTER_MD_RE = re.compile(r"^## (제\d+(?:편|장|절).+?)\s*$")
# 조문 헤더 — 닫는 괄호 뒤에 본문이 같은 줄에 붙어도 인식(group5=인라인 본문).
# kordoc 추출본은 "### 제9조(조직) ① 협력단의…"처럼 본문을 헤더에 붙이는 경우가 많다.
ARTICLE_MD_RE = re.compile(r"^#{2,3} (제(\d+)조(?:의(\d+))?)\s*\((.+?)\)\s*(.*)$")
# Format B (PDF 평문): "      제1장 총칙", "제1조(목적) 본문…"
CHAPTER_PLAIN_RE = re.compile(r"^\s*(제\d+(?:편|장|절))\s+(\S.{0,40})\s*$")
ARTICLE_PLAIN_RE = re.compile(r"^\s*제(\d+)조(?:의(\d+))?\s*\(([^)]+)\)\s*(.*)$")
# 부칙(附則) 경계 마커 — 이 라인 이후의 조문은 부칙 조문(본칙 제N조와 번호 충돌).
# 예: "부칙 <1991.04.24.>", "부 칙 <2016.07.19.>", "**부칙 <…>**"
SUPPL_MARK_RE = re.compile(r"^\**\s*부\s*칙(?:\s*<|\s*\**\s*$|\s)")

KOREAN_JOSA = (
    "에게서", "으로부터", "로부터", "에서", "께서", "에게", "한테",
    "으로", "이라", "라고", "이며", "이고",
    "을", "를", "이", "가", "은", "는", "에", "와", "과", "의", "도", "만", "로", "라",
)
JOSA_RE = re.compile(rf"({'|'.join(KOREAN_JOSA)})$")

# 검색 노이즈 — 시험문제 상투어 + 도메인 흔한 표현
STOPWORDS = {
    # 시험문제 상투어
    "다음", "중", "옳은", "옳지", "않은", "것은", "것", "것이", "것을",
    "어느", "어떤", "해당", "어떻게", "얼마", "약", "몇", "최소", "최대",
    "이며", "이고", "이다", "한다", "있다", "없다", "이상", "이하", "초과", "미만",
    "포함", "제외", "관한", "관하여", "대한", "대하여", "위한", "위해", "위하여",
    "그리고", "그러나", "만약", "또는", "다만", "단", "각", "각호", "이하의",
    "사항", "내용", "설명", "경우", "방법", "기준", "원칙", "규정",
    "통해", "통하여", "라고", "라면", "라는",
    # 한 글자 noise
    "수", "또", "더", "곧", "왜", "뭐", "이",
}


@dataclass
class Article:
    category: str
    source: str
    revision: str
    file: str
    chapter: str
    article: str
    article_no: int
    article_sub: int
    article_title: str
    body: str
    is_supplementary: bool = False  # 부칙(附則) 조문 여부 — 본칙 제N조와 번호 충돌 구분용

    @property
    def citation(self) -> str:
        return f"{self.source} {self.article}"


@dataclass
class Attachment:
    """규정의 별표·별지 (행정처분 기준표, 서식 등)."""
    category: str
    source: str
    revision: str
    file: str
    kind: str          # "별표" / "별지"
    label: str         # 원본 라벨 그대로 (예: "[별표 1]", "[별지 제3호 서식]")
    number: str        # 번호 부분만 (예: "1", "1-1", "제3호")
    title: str         # 라벨 뒤 제목
    body: str
    deleted: bool      # 본문에 <삭제 ...> 메타가 있는지

    @property
    def citation(self) -> str:
        return f"{self.source} {self.label}"


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# source 부분일치 정규화: 공백 제거 → 흔한 한국어 연결어 제거
# (한 번에 alternation 하면 공백 사이 연결어가 매칭 안 됨 → 2단계 처리)
_SOURCE_CONNECTOR_RE = re.compile(r"에관한|관한|의|및|와|과")


def _normalize_source(s: str) -> str:
    # 공백 + 유니코드 중점 변종(·U+00B7 ․U+2024 ‧U+2027 ・U+30FB) 제거 후 연결어 제거.
    # 문서마다 "설치·운영"/"설치․운영" 표기가 갈려 정비 레이더의 모규정 매칭이 새던 것 보정.
    s = re.sub(r"[\s·․‧・]+", "", _nfc(s))
    return _SOURCE_CONNECTOR_RE.sub("", s)


def source_match(query: str, source_label: str) -> bool:
    """source 부분일치 매칭 (3단계).

    1) 직접 substring — "인사규정" in "인사규정 시행세칙"
    2) 공백 토큰 모두 등장 — "공공기관 운영" → 두 토큰 모두 등장
    3) 정규화 substring (공백·연결어 제거) — "공공기관운영"이 "공공기관의 운영에 관한 법률"에 매칭
    """
    if not query:
        return True
    q = _nfc(query).strip()
    s = _nfc(source_label)
    if not q:
        return True
    if q in s:
        return True
    tokens = [t for t in re.split(r"\s+", q) if len(t) >= 2]
    if tokens and all(t in s for t in tokens):
        return True
    nq = _normalize_source(q)
    if nq and nq in _normalize_source(s):
        return True
    return False


# 단락 시작 마커 — 이 줄은 이전 줄과 합치지 않고 새 단락으로 시작
_PARA_START_RE = re.compile(
    r"^("
    r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]"      # 항
    r"|\d+(?:\s*의\s*\d+)?[\.\)]\s"             # 호 1. / 1) / 1의2.
    r"|[가-힣]\.\s"                              # 목 가. 나.
    r"|\[(?:별표|별지|서식)"                     # [별표 1], [별지 …]
    r")"
)


def reflow(text: str) -> str:
    """PDF 평문 추출로 끊긴 줄을 단락 단위로 합쳐 가독성을 복원.

    유지: 빈 줄, 항(①), 호(1./1)), 목(가.), [별표/별지] 시작 라인.
    합침: 그 외 연속 줄은 단일 공백으로 결합 후 다중 공백 정리.
    """
    lines = text.split("\n")
    paragraphs: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        nonlocal cur
        if cur:
            joined = re.sub(r"[ \t]+", " ", " ".join(cur)).strip()
            if joined:
                paragraphs.append(joined)
        cur = []

    for raw in lines:
        s = raw.strip()
        if not s:
            flush()
            if paragraphs and paragraphs[-1] != "":
                paragraphs.append("")
            continue
        if _PARA_START_RE.match(s):
            flush()
            cur.append(s)
        else:
            cur.append(s)
    flush()

    # 끝의 빈 줄 제거
    while paragraphs and paragraphs[-1] == "":
        paragraphs.pop()
    return "\n".join(paragraphs)


def parse_md(path: Path, category: str) -> tuple[list[Article], list[Attachment]]:
    text = _nfc(path.read_text(encoding="utf-8"))
    lines = text.splitlines()

    source = _nfc(path.stem)
    revision = ""
    chapter = ""
    in_supplementary = False   # 부칙 구간 진입 여부
    articles: list[Article] = []
    attachments: list[Attachment] = []
    cur: Optional[dict] = None
    body_lines: list[str] = []
    # 별표·별지 캡처 상태
    in_attachment: Optional[dict] = None
    att_body_lines: list[str] = []

    def flush_att() -> None:
        """현재 진행 중인 별표·별지를 attachments에 추가."""
        nonlocal in_attachment, att_body_lines
        if in_attachment is not None:
            body = reflow("\n".join(att_body_lines)).strip()
            deleted = bool(re.search(r"<\s*삭제\s*[^>]*>", body)) or "삭제" in in_attachment["title"]
            attachments.append(Attachment(
                category=category,
                source=source,
                revision=revision,
                file=str(path.relative_to(ROOT)),
                kind=in_attachment["kind"],
                label=in_attachment["label"],
                number=in_attachment["number"],
                title=in_attachment["title"],
                body=body,
                deleted=deleted,
            ))
        in_attachment = None
        att_body_lines = []

    def flush() -> None:
        nonlocal cur, body_lines
        flush_att()
        if cur is not None:
            articles.append(Article(
                category=category,
                source=source,
                revision=revision,
                file=str(path.relative_to(ROOT)),
                chapter=chapter,
                body=reflow("\n".join(body_lines)),
                **cur,
            ))
        cur = None
        body_lines = []

    def open_attachment(kind: str, number: str, title: str) -> None:
        nonlocal in_attachment, att_body_lines
        flush_att()
        # 진행 중 조문 body는 attachment 직전까지로 닫고, 다음 조문이 나오면 새 조문 시작
        # 단, 조문 자체를 닫지는 않음 — 같은 조문 안에 attachment만 분리되는 경우도 있을 수 있어
        # 일반적으로는 별표가 규정 마지막에 모이므로 안전.
        num = re.sub(r"\s+", "", number.replace("호", "호"))
        # 라벨 정규화 (공백 정리)
        label_inner = f"{kind} {number}".strip()
        label_inner = re.sub(r"\s+", " ", label_inner)
        in_attachment = {
            "kind": kind,
            "label": f"[{label_inner}]",
            "number": re.sub(r"\s+", "", number),
            "title": title.strip(),
        }
        att_body_lines = []

    def open_article(no: int, sub: int, title: str, rest: str) -> None:
        nonlocal cur
        flush()
        art = f"제{no}조" + (f"의{sub}" if sub else "")
        cur = {
            "article": art,
            "article_no": no,
            "article_sub": sub,
            "article_title": title.strip(),
            "is_supplementary": in_supplementary,
        }
        if rest:
            body_lines.append(rest.strip())

    def absorb_attachment_line(line: str) -> bool:
        """line 안의 모든 [별표/별지] 헤더를 분리 처리. 처리 성공 시 True."""
        matches = list(_ATTACHMENT_HEAD_RE.finditer(line))
        if not matches:
            return False
        # 첫 매칭 이전 부분은 직전 attachment의 body 마지막 줄로 (없으면 무시)
        prefix = line[: matches[0].start()].strip()
        if prefix and in_attachment is not None:
            att_body_lines.append(prefix)
        # 각 매칭을 attachment로 열고, 매칭 사이의 텍스트를 그 attachment의 body 시작으로
        for i, m in enumerate(matches):
            kind = m.group("kind")
            number = m.group("number")
            after_start = m.end()
            after_end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            tail = line[after_start:after_end].strip()
            # tail은 보통 "제목 <메타>" 형태. 줄 끝까지를 title + 본문 첫 줄로
            open_attachment(kind, number, tail)
        return True

    for line in lines:
        # 헤더 (문서 첫 줄)
        if line.startswith("# "):
            m = HEADER_RE.match(line)
            if m:
                source = m.group(1).strip()
                revision = (m.group(2) or "").strip()
            continue

        # 부칙 경계 — 이후 조문은 부칙 조문으로 표시 (본칙 제N조와 번호 충돌 구분)
        if SUPPL_MARK_RE.match(line.strip()):
            in_supplementary = True
            continue

        # 별표·별지 라인 — 어디서든 등장 가능 (조문 진행 중에도)
        if "[별표" in line or "[별지" in line:
            if absorb_attachment_line(line):
                continue
        # Format A: 마크다운 ## / ### (조문은 ##·### 모두 허용)
        if line.startswith("##"):
            m_art = ARTICLE_MD_RE.match(line)
            if m_art:
                open_article(int(m_art.group(2)), int(m_art.group(3) or 0),
                             m_art.group(4), m_art.group(5) or "")
                continue
            if line.startswith("## "):
                flush()
                m_ch = CHAPTER_MD_RE.match(line)
                chapter = m_ch.group(1).strip() if m_ch else line[3:].strip()
                continue
            if line.startswith("### "):
                flush()
                continue
        # Format B: 평문 PDF
        stripped = line.strip()
        m_ch = CHAPTER_PLAIN_RE.match(line)
        if m_ch and len(stripped) <= 30 and not stripped.endswith(("다.", "한다.", "있다.", "없다.")):
            chapter = f"{m_ch.group(1)} {m_ch.group(2).strip()}"
            continue
        m_art = ARTICLE_PLAIN_RE.match(line)
        if m_art and (not line.startswith(" ") or len(line) - len(line.lstrip()) <= 2):
            # 들여쓰기 3칸 이상은 본문 안 인용으로 보고 무시
            open_article(
                int(m_art.group(1)),
                int(m_art.group(2) or 0),
                m_art.group(3),
                m_art.group(4),
            )
            continue
        if in_attachment is not None:
            att_body_lines.append(line.strip())
        elif cur is not None:
            body_lines.append(line.strip())

    flush()
    return articles, attachments


def build_index(
    build_semantic: bool = True,
    strict_semantic: bool = False,
) -> tuple[list[Article], list[Attachment]]:
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"data 폴더 없음: {DATA_DIR}")
    articles: list[Article] = []
    attachments: list[Attachment] = []
    skipped = []
    for md in sorted(DATA_DIR.glob("*.md")):
        if md.name in CATEGORY_AGGREGATE_FILES:
            skipped.append(md.name)
            continue
        category = md.stem.split("_", 1)[0]
        arts, atts = parse_md(md, category)
        articles.extend(arts)
        attachments.extend(atts)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(
            {
                "version": 2,
                "articles": [asdict(a) for a in articles],
                "attachments": [asdict(a) for a in attachments],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"빌드: {len(articles)}개 조문 + {len(attachments)}개 별표·별지 / 합본 스킵 {len(skipped)}개 → {INDEX_PATH}",
        file=sys.stderr,
    )

    global _INDEX_CACHE, _ATTACHMENT_CACHE
    _INDEX_CACHE = None
    _ATTACHMENT_CACHE = None

    if build_semantic:
        try:
            import semantic_search

            metadata = semantic_search.build_semantic_index(
                articles,
                attachments,
                index_path=INDEX_PATH,
                vectors_path=SEMANTIC_VECTORS_PATH,
                meta_path=SEMANTIC_META_PATH,
            )
            print(
                f"의미 인덱스: {metadata['vector_count']}개 청크 → {SEMANTIC_VECTORS_PATH}",
                file=sys.stderr,
            )
        except Exception as exc:
            # 개발 환경에서 선택 의존성/모델이 없더라도 기존 키워드 검색은 유지한다.
            # 배포 이미지는 --strict-semantic으로 빌드하므로 이 경로를 허용하지 않는다.
            try:
                import semantic_search

                semantic_search.reset_caches()
            except Exception:
                pass
            if strict_semantic:
                raise RuntimeError(f"의미 인덱스 빌드 실패: {exc}") from exc
            print(
                f"경고: 의미 인덱스를 만들지 못해 키워드 검색만 사용합니다: {exc}",
                file=sys.stderr,
            )
    return articles, attachments


_INDEX_CACHE: Optional[list[Article]] = None
_ATTACHMENT_CACHE: Optional[list[Attachment]] = None


def _read_index_file() -> tuple[list[Article], list[Attachment]]:
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    # v2 형식: {articles: [...], attachments: [...]}
    # v1 형식 (호환성): list[Article]
    if isinstance(raw, dict) and "articles" in raw:
        arts = [Article(**r) for r in raw["articles"]]
        atts = [Attachment(**r) for r in raw.get("attachments", [])]
    else:
        arts = [Article(**r) for r in raw]
        atts = []
    return arts, atts


def load_index(use_cache: bool = True) -> list[Article]:
    global _INDEX_CACHE, _ATTACHMENT_CACHE
    if use_cache and _INDEX_CACHE is not None:
        return _INDEX_CACHE
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"인덱스 없음. 먼저 'build' 실행: {INDEX_PATH}")
    arts, atts = _read_index_file()
    if use_cache:
        _INDEX_CACHE = arts
        _ATTACHMENT_CACHE = atts
    return arts


def load_attachments(use_cache: bool = True) -> list[Attachment]:
    global _ATTACHMENT_CACHE
    if use_cache and _ATTACHMENT_CACHE is not None:
        return _ATTACHMENT_CACHE
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"인덱스 없음. 먼저 'build' 실행: {INDEX_PATH}")
    arts, atts = _read_index_file()
    if use_cache:
        _INDEX_CACHE = arts
        _ATTACHMENT_CACHE = atts
    return atts


def tokenize(query: str) -> list[str]:
    query = _nfc(query)
    raw_tokens: list[str] = []
    for tok in re.split(r"[\s,·、]+", query.strip()):
        tok = tok.strip("().,!?\"'·")
        if not tok:
            continue
        stripped = JOSA_RE.sub("", tok)
        candidate = stripped if len(stripped) >= 2 else tok
        if candidate in STOPWORDS or len(candidate) < 2:
            continue
        raw_tokens.append(candidate)
    # dedup, preserve order
    seen = set()
    return [t for t in raw_tokens if not (t in seen or seen.add(t))]


def compute_idf(tokens: list[str], articles: list[Article]) -> dict[str, float]:
    """쿼리 토큰별 IDF (등장 조문 수의 역수 가중)."""
    n = len(articles)
    idf: dict[str, float] = {}
    for t in tokens:
        df = sum(1 for a in articles if t in a.body or t in a.article_title or t in a.chapter)
        idf[t] = math.log((n + 1) / (df + 1)) + 1.0
    return idf


def _bigrams(s: str) -> list[str]:
    """음절 2-gram 분해. 'koica' → ['ko','oi','ic','ca'], '사례금' → ['사례','례금']."""
    return [s[i:i + 2] for i in range(len(s) - 1)]


def score_article(
    a: Article,
    tokens: list[str],
    idf: Optional[dict[str, float]] = None,
    fuzzy: bool = False,
) -> tuple[float, int]:
    score = 0.0
    first_pos = -1
    body = a.body
    for tok in tokens:
        w = idf[tok] if idf else 1.0
        if tok in a.article_title:
            score += 5.0 * w
        if tok in a.chapter:
            score += 2.0 * w
        cnt = body.count(tok)
        if cnt:
            score += float(cnt) * w
            pos = body.find(tok)
            if first_pos < 0 or pos < first_pos:
                first_pos = pos
        elif fuzzy and len(tok) >= 3:
            # 정확 매칭 실패 + fuzzy 모드: bi-gram 부분 매칭
            # "사례비" 검색 시 본문 "사례금"의 "사례" bigram에 점수 부여
            bgs = _bigrams(tok)
            bg_match_count = sum(1 for b in bgs if b in body)
            if bg_match_count >= len(bgs) * 0.5:
                bg_hits = sum(body.count(b) for b in bgs)
                score += (bg_hits / len(bgs)) * 0.3 * w
                if first_pos < 0:
                    for b in bgs:
                        p = body.find(b)
                        if p >= 0:
                            first_pos = p
                            break
    return score, first_pos


# 본문 메타 태그 — snippet 출력 가독성을 위해 정리
# 예: <개정 2018.12.28., 2025.06.27.>, <신설 2022.07.12.>, [제목개정 2023.10.06.]
_META_NOISE_RE = re.compile(
    r"<(?:개정|신설|삭제|단서개정|제목개정|전부개정)[^>]*?>"
    r"|\[(?:개정|신설|삭제|제목개정|단서개정|전부개정)[^\]]*?\]"
)


def _strip_meta(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _META_NOISE_RE.sub("", text)).strip()


def make_snippet(body: str, pos: int, span: int = 80) -> str:
    if not body:
        return ""
    if pos < 0:
        s = body[: span * 2].replace("\n", " ")
        s = _strip_meta(s)
        return s + ("…" if len(body) > span * 2 else "")
    start = max(0, pos - span)
    end = min(len(body), pos + span)
    s = _strip_meta(body[start:end].replace("\n", " "))
    if start > 0:
        s = "…" + s
    if end < len(body):
        s = s + "…"
    return s


HYBRID_CANDIDATE_LIMIT = 50
RRF_K = 60
# 두 채널을 같은 비중으로 결합해야 의미 후보와 정확 키워드 후보가 모두 상위권에
# 남는다. 한쪽 가중치를 과도하게 높이면 다른 채널의 단독 후보가 사실상 사라진다.
SEMANTIC_RRF_WEIGHT = 1.0
SEARCH_MODES = {"hybrid", "keyword", "semantic"}
_SEMANTIC_WARNING_EMITTED = False


def _attachment_score(
    attachment: Attachment,
    tokens: list[str],
    idf: dict[str, float],
) -> tuple[float, int]:
    score = 0.0
    first_pos = -1
    for token in tokens:
        weight = idf.get(token, 1.0)
        if token in attachment.title:
            score += 5.0 * weight
        count = attachment.body.count(token)
        if count:
            score += float(count) * weight
            position = attachment.body.find(token)
            if first_pos < 0 or position < first_pos:
                first_pos = position
    return score, first_pos


def _rrf_fuse(
    keyword_keys: list[tuple[str, int]],
    semantic_keys: list[tuple[str, int]],
    *,
    k: int = RRF_K,
    semantic_weight: float = SEMANTIC_RRF_WEIGHT,
) -> list[tuple[tuple[str, int], float]]:
    """Fuse two ranked lists without comparing their incompatible raw scores."""

    fused: dict[tuple[str, int], float] = {}
    first_seen: dict[tuple[str, int], tuple[int, int]] = {}
    for rank, key in enumerate(keyword_keys, 1):
        fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
        first_seen.setdefault(key, (0, rank))
    for rank, key in enumerate(semantic_keys, 1):
        fused[key] = fused.get(key, 0.0) + semantic_weight / (k + rank)
        first_seen.setdefault(key, (1, rank))
    return sorted(
        fused.items(),
        key=lambda row: (-row[1], first_seen[row[0]], row[0]),
    )


def _preserve_channel_heads(
    ranked: list[tuple[tuple[str, int], float]],
    keyword_keys: list[tuple[str, int]],
    semantic_keys: list[tuple[str, int]],
    *,
    limit: int,
) -> list[tuple[tuple[str, int], float]]:
    """Keep each channel's first hit when at least two result slots exist.

    RRF correctly rewards candidates found by both channels, but many consensus
    hits can otherwise push a semantic-only paraphrase (or an exact keyword-only
    citation) just outside a small result window.  Reserving one slot for each
    channel head preserves both recall paths without changing their RRF scores.
    """

    selected = list(ranked[:limit])
    if limit < 2 or not keyword_keys or not semantic_keys:
        return selected

    required = list(dict.fromkeys((keyword_keys[0], semantic_keys[0])))
    if len(required) < 2:
        return selected
    score_by_key = dict(ranked)
    selected_keys = {key for key, _score in selected}
    for required_key in required:
        if required_key in selected_keys:
            continue
        replace_at = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if selected[index][0] not in required
            ),
            None,
        )
        if replace_at is None:
            break
        selected_keys.discard(selected[replace_at][0])
        selected[replace_at] = (required_key, score_by_key[required_key])
        selected_keys.add(required_key)

    original_rank = {key: index for index, (key, _score) in enumerate(ranked)}
    selected.sort(key=lambda row: original_rank[row[0]])
    return selected


def _search_result(
    *,
    key: tuple[str, int],
    item: Article | Attachment,
    position: int,
    score: float,
    search_mode: str,
    keyword_score: Optional[float],
    semantic_score: Optional[float],
) -> dict:
    score_digits = (
        2
        if search_mode.startswith("keyword")
        else 4
        if search_mode == "semantic"
        else 6
    )
    common = {
        "type": key[0],
        "category": item.category,
        "source": item.source,
        "revision": item.revision,
        "citation": item.citation,
        "snippet": make_snippet(item.body, position),
        "score": round(score, score_digits),
        "search_mode": search_mode,
        "matched_by": (
            "hybrid"
            if keyword_score is not None and semantic_score is not None
            else "keyword"
            if keyword_score is not None
            else "semantic"
        ),
        "keyword_score": round(keyword_score, 2) if keyword_score is not None else None,
        "semantic_score": round(semantic_score, 4) if semantic_score is not None else None,
    }
    if key[0] == "article":
        article = item
        assert isinstance(article, Article)
        return {
            **common,
            "chapter": article.chapter,
            "article": article.article,
            "article_title": article.article_title,
        }
    attachment = item
    assert isinstance(attachment, Attachment)
    return {
        **common,
        "kind": attachment.kind,
        "label": attachment.label,
        "title": attachment.title,
    }


def search(
    query: str,
    category: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 10,
    fuzzy: bool = False,
    include_attachments: bool = False,
    mode: str = "hybrid",
) -> list[dict]:
    """Search regulations with keyword, semantic, or fused hybrid retrieval.

    ``hybrid`` is the default.  If the optional model or dense artifacts are not
    available, it degrades to the exact same keyword ranking and labels the response
    ``keyword_fallback`` rather than failing the MCP request.
    """

    global _SEMANTIC_WARNING_EMITTED
    normalized_mode = mode.lower().strip()
    if normalized_mode not in SEARCH_MODES:
        choices = ", ".join(sorted(SEARCH_MODES))
        raise ValueError(f"mode는 다음 중 하나여야 합니다: {choices}")
    if not query.strip() or limit <= 0:
        return []

    articles = load_index()
    attachments = load_attachments()
    article_allowed = [
        (not category or article.category == category)
        and (not source or source_match(source, article.source))
        for article in articles
    ]
    attachment_allowed = [
        include_attachments
        and not attachment.deleted
        and (not category or attachment.category == category)
        and (not source or source_match(source, attachment.source))
        for attachment in attachments
    ]

    candidate_limit = max(limit, HYBRID_CANDIDATE_LIMIT)

    def rank_keyword() -> list[tuple[float, int, tuple[str, int]]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        idf = compute_idf(tokens, articles)
        rows: list[tuple[float, int, tuple[str, int]]] = []
        for item_index, article in enumerate(articles):
            if not article_allowed[item_index]:
                continue
            raw_score, position = score_article(article, tokens, idf, fuzzy=fuzzy)
            if raw_score > 0:
                rows.append((raw_score, position, ("article", item_index)))

        for item_index, attachment in enumerate(attachments):
            if not attachment_allowed[item_index]:
                continue
            raw_score, position = _attachment_score(attachment, tokens, idf)
            if raw_score > 0:
                rows.append((raw_score, position, ("attachment", item_index)))
        rows.sort(key=lambda row: row[0], reverse=True)
        return rows[:candidate_limit]

    # Explicit semantic mode does not tokenize, compute IDF, or scan the corpus unless
    # dense retrieval actually fails and the documented keyword fallback is needed.
    keyword_rows = rank_keyword() if normalized_mode in {"hybrid", "keyword"} else []

    semantic_rows: list[dict] = []
    semantic_failed = False
    if normalized_mode in {"hybrid", "semantic"}:
        try:
            import semantic_search

            semantic_rows = semantic_search.semantic_rank(
                query,
                article_allowed=article_allowed,
                attachment_allowed=attachment_allowed,
                limit=candidate_limit,
                index_path=INDEX_PATH,
                vectors_path=SEMANTIC_VECTORS_PATH,
                meta_path=SEMANTIC_META_PATH,
            )
        except Exception as exc:
            semantic_failed = True
            if not _SEMANTIC_WARNING_EMITTED:
                print(
                    f"경고: 의미 검색을 사용할 수 없어 키워드 검색으로 대체합니다: {exc}",
                    file=sys.stderr,
                )
                _SEMANTIC_WARNING_EMITTED = True

    if semantic_failed and normalized_mode == "semantic":
        keyword_rows = rank_keyword()

    keyword_details = {
        key: {"score": raw_score, "position": position}
        for raw_score, position, key in keyword_rows
    }

    semantic_details = {
        (row["kind"], row["item_index"]): row for row in semantic_rows
    }

    if normalized_mode == "keyword" or semantic_failed:
        ranked = [(key, details["score"]) for key, details in keyword_details.items()]
        effective_mode = "keyword" if normalized_mode == "keyword" else "keyword_fallback"
    elif normalized_mode == "semantic":
        ranked = [
            ((row["kind"], row["item_index"]), row["score"])
            for row in semantic_rows
        ]
        effective_mode = "semantic"
    else:
        keyword_keys = list(keyword_details)
        semantic_keys = [
            (row["kind"], row["item_index"]) for row in semantic_rows
        ]
        ranked = _rrf_fuse(
            keyword_keys,
            semantic_keys,
        )
        ranked = _preserve_channel_heads(
            ranked,
            keyword_keys,
            semantic_keys,
            limit=limit,
        )
        effective_mode = "hybrid"

    output: list[dict] = []
    for key, fused_score in ranked[:limit]:
        kind, item_index = key
        item: Article | Attachment
        item = articles[item_index] if kind == "article" else attachments[item_index]
        keyword_detail = keyword_details.get(key)
        semantic_detail = semantic_details.get(key)
        if keyword_detail is not None:
            position = int(keyword_detail["position"])
        elif semantic_detail is not None:
            position = (int(semantic_detail["start"]) + int(semantic_detail["end"])) // 2
        else:  # Defensive only: every ranked key came from one of the channels.
            position = -1
        output.append(
            _search_result(
                key=key,
                item=item,
                position=position,
                score=float(fused_score),
                search_mode=effective_mode,
                keyword_score=(
                    float(keyword_detail["score"]) if keyword_detail is not None else None
                ),
                semantic_score=(
                    float(semantic_detail["score"]) if semantic_detail is not None else None
                ),
            )
        )
    return output


ARTICLE_TOKEN_RE = re.compile(r"^\s*(?:제)?(\d+)조?(?:의(\d+))?\s*$")
# 인용: 제N조[의M] [제N항][제N호] 뒤에 (조문제목)이 붙으면 내용검증에 사용
CITATION_RE = re.compile(
    r"제(\d+)조(?:의(\d+))?(?:\s*제\d+항)?(?:\s*제\d+호)?(?:\s*\(([^)]{2,40})\))?"
)
# 조문 직후 괄호가 제목이 아니라 정의·부연(예: "(이하 '직원')")이면 제목검증 대상 제외
_DEF_PAREN_RE = re.compile(r"이하|약칭|['\"‘’“”]|(?:이)?라\s*(?:한다|칭한다)")


def _title_key(s: str) -> str:
    """제목 비교용 정규화 — 공백·문장부호 제거."""
    return re.sub(r"[\s·․.,'\"()\[\]「」]", "", _nfc(s))


def _title_matches(cited: str, actual: str) -> bool:
    """인용에 붙은 조문제목이 실제 제목과 부합하는지 판정.

    - 정확일치, 또는 **인용 제목이 실제 제목의 부분**(축약 인용: "정의"⊂"용어의 정의")은 일치.
    - 반대로 **인용이 실제보다 길어 별도 수식어를 붙인 경우**("육아휴직" vs 실제 "휴직")는
      환각으로 보고 불일치 처리 — 이전의 `a in c` 통과를 제거한 핵심 수정.
    - 그 밖엔 음절 bigram Jaccard ≥ 0.4 이면 이표기로 보고 일치.
    """
    c, a = _title_key(cited), _title_key(actual)
    if not c or not a:
        return True
    if c == a or c in a:   # 인용이 실제의 부분(축약)은 허용, 그 역(수식어 추가)은 불허
        return True
    cb, ab = set(_bigrams(c)), set(_bigrams(a))
    if not cb or not ab:
        return True
    jac = len(cb & ab) / len(cb | ab)
    return jac >= 0.4


def _parse_article_token(token: str) -> Optional[tuple[int, int]]:
    """\"제11조\", \"11\", \"15의2\", \"제15조의2\" 등 → (11,0) / (15,2)."""
    t = _nfc(token).strip()
    m = ARTICLE_TOKEN_RE.match(t)
    if m:
        return int(m.group(1)), int(m.group(2) or 0)
    m2 = re.search(r"제(\d+)조(?:의(\d+))?", t)
    if m2:
        return int(m2.group(1)), int(m2.group(2) or 0)
    return None


def _source_selector(query: Optional[str], articles: list[Article]):
    """source 매칭 술어. 질의가 어떤 규정명과 **정확 일치(NFC)** 하면 그 규정으로
    한정하고, 아니면 부분일치(source_match). '직제규정'이 '직제규정 시행세칙'까지
    번지는 모호성을 차단한다."""
    if not query:
        return lambda s: True
    q = _nfc(query).strip()
    if any(a.source == q for a in articles):
        return lambda s: s == q
    return lambda s: source_match(query, s)


def get_article(source: str, article: str) -> list[dict]:
    """source 매칭(정확일치 우선) + article 정확매칭으로 조문 본문 반환.

    본칙(비-부칙) 조문을 우선한다 — 본칙 매칭이 있으면 부칙 제N조는 제외.
    (부칙 제1조/제2조가 본칙과 같은 번호로 중복되던 오염을 해소.)
    """
    parsed = _parse_article_token(article)
    if parsed is None:
        return []
    no, sub = parsed
    arts = load_index()
    src_ok = _source_selector(source, arts)
    matches = [a for a in arts
               if src_ok(a.source) and a.article_no == no and a.article_sub == sub]
    main = [a for a in matches if not a.is_supplementary]
    chosen = main if main else matches
    return [{
        "category": a.category,
        "source": a.source,
        "revision": a.revision,
        "chapter": a.chapter,
        "article": a.article,
        "article_title": a.article_title,
        "citation": a.citation,
        "body": a.body,
        "is_supplementary": a.is_supplementary,
    } for a in chosen]


def _article_range_for(source_nfc: str, articles: list[Article]) -> str:
    nos = sorted({(a.article_no, a.article_sub) for a in articles if source_nfc in a.source})
    if not nos:
        return "(해당 source 없음)"
    first = f"제{nos[0][0]}조" + (f"의{nos[0][1]}" if nos[0][1] else "")
    last = f"제{nos[-1][0]}조" + (f"의{nos[-1][1]}" if nos[-1][1] else "")
    return f"{first} ~ {last}, 총 {len(nos)}개"


# verify_citation은 인증 없는 공개 원격 서버에서 호출되고, 동기 도구는 서버의
# 이벤트 루프에서 직접 실행된다. 입력 길이와 처리할 인용 수에 상한이 없으면 요청
# 하나가 CPU와 메모리를 모두 점유해 다른 모든 클라이언트를 멈춰 세운다.
VERIFY_MAX_TEXT_CHARS = 20_000
VERIFY_MAX_CITATIONS = 200


def verify_citation(text: str) -> list[dict]:
    """텍스트 내 모든 '{규정명} 제N조[의M]' 인용을 인덱스로 교차검증.

    각 인용에 대해 status:
      - ok: 규정·조문 실재 (인용에 조문제목이 붙었으면 제목까지 일치)
      - content_mismatch: 조문은 실재하나 붙은 제목이 실제와 다름 (내용 환각)
      - not_found: 규정은 알지만 해당 조문 없음
      - unknown_source: 직전 텍스트에서 알려진 규정명을 못 찾음

    content_mismatch는 "인사규정 제11조(육아휴직)"처럼 존재하는 조문번호에
    엉뚱한 제목을 붙인 LLM 환각을 잡는다.

    입력이 VERIFY_MAX_TEXT_CHARS자를 넘거나 인용이 VERIFY_MAX_CITATIONS건을
    넘으면 앞부분만 검증하고, 마지막에 status="truncated" 알림 항목을 덧붙인다.
    """
    truncated_text = len(text) > VERIFY_MAX_TEXT_CHARS
    text_nfc = _nfc(text[:VERIFY_MAX_TEXT_CHARS])
    articles = load_index()
    known_sources = sorted({a.source for a in articles}, key=len, reverse=True)

    # 인용 1건마다 전체 조문을 훑지 않도록 (규정명, 조번호, 가지번호) 색인을
    # 한 번만 만든다. 인용이 하나뿐인 일반 호출에서도 기존 선형 탐색보다 빠르다.
    by_key: dict[tuple[str, int, int], list[Article]] = {}
    for a in articles:
        by_key.setdefault((a.source, a.article_no, a.article_sub), []).append(a)

    # not_found 안내에 쓰는 조문 범위도 규정명마다 한 번만 계산한다.
    range_cache: dict[str, str] = {}

    def article_range(src: str) -> str:
        if src not in range_cache:
            range_cache[src] = _article_range_for(src, articles)
        return range_cache[src]

    def nearest_source(prefix: str) -> Optional[str]:
        best, best_pos = None, -1
        for src in known_sources:
            pos = prefix.rfind(src)
            # 끝에 가까운 매칭 우선, 동률이면 더 긴 라벨
            if pos > best_pos or (pos == best_pos and best and len(src) > len(best)):
                best_pos, best = pos, src
        return best if best_pos >= 0 else None

    results = []
    skipped = 0
    for m in CITATION_RE.finditer(text_nfc):
        # 각 분기가 정확히 1건씩 append하므로 len(results)가 곧 처리한 인용 수다.
        if len(results) >= VERIFY_MAX_CITATIONS:
            skipped += 1
            continue
        prefix = text_nfc[max(0, m.start() - 80): m.start()]
        matched_src = nearest_source(prefix)
        art = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        full_cite = text_nfc[m.start(): m.end()]
        if not matched_src:
            results.append({
                "citation": full_cite,
                "status": "unknown_source",
                "message": "직전 텍스트에서 알려진 규정명을 찾지 못함",
            })
            continue
        no, sub = int(m.group(1)), int(m.group(2) or 0)
        cand = by_key.get((matched_src, no, sub), [])
        # 인용 직전에 "부칙"이 있으면 부칙 조문 우선, 아니면 본칙 우선
        prefer_suppl = "부칙" in text_nfc[max(0, m.start() - 15): m.start()]
        main = [a for a in cand if not a.is_supplementary]
        suppl = [a for a in cand if a.is_supplementary]
        ordered = (suppl + main) if prefer_suppl else (main + suppl)
        hit = ordered[0] if ordered else None
        if hit:
            cited_title = (m.group(3) or "").strip()
            # 정의·부연 괄호(이하 …)는 제목이 아니므로 내용검증 제외
            check_title = bool(cited_title) and not _DEF_PAREN_RE.search(cited_title)
            if check_title and not _title_matches(cited_title, hit.article_title):
                results.append({
                    "citation": f"{matched_src} {art}",
                    "raw_match": full_cite,
                    "status": "content_mismatch",
                    "cited_title": cited_title,
                    "actual_title": hit.article_title,
                    "message": f"{matched_src} {art}의 실제 제목은 '{hit.article_title}' — "
                               f"인용에 붙은 '{cited_title}'와 불일치(내용 환각 가능)",
                })
            else:
                results.append({
                    "citation": f"{matched_src} {art}",
                    "raw_match": full_cite,
                    "status": "ok",
                    "article_title": hit.article_title,
                    "title_verified": check_title,
                    "body_excerpt": _strip_meta(hit.body[:250].replace("\n", " "))[:150],
                })
        else:
            results.append({
                "citation": f"{matched_src} {art}",
                "raw_match": full_cite,
                "status": "not_found",
                "message": f"{matched_src}에 {art} 없음 (실재: {article_range(matched_src)})",
            })

    if truncated_text or skipped:
        notes = []
        if truncated_text:
            notes.append(f"입력이 {VERIFY_MAX_TEXT_CHARS}자를 넘어 앞부분만 검증했습니다")
        if skipped:
            notes.append(
                f"인용 {VERIFY_MAX_CITATIONS}건까지만 검증했습니다(미검증 {skipped}건)"
            )
        results.append({
            "status": "truncated",
            "verified": len(results),
            "skipped": skipped,
            "message": ". ".join(notes) + ". 텍스트를 나눠 다시 요청하세요.",
        })
    return results


# 같은 규정 안의 "제N조" 인용 (앞에 규정명이 안 붙은 경우)
_SAME_REG_CITE_RE = re.compile(r"(?<![가-힣A-Za-z\w])제(\d+)조(?:의(\d+))?")
# 외부 규정 인용: "「법령명」 제N조" 또는 "{규정명} 제N조"
_EXTERNAL_CITE_RE = re.compile(
    r"(?:「([^」\n]{2,40}?)」|((?:[가-힣]+\s?){1,6}?(?:규정|법률|법|지침|세칙|정관|매뉴얼)))\s*제(\d+)조(?:의(\d+))?"
)


def _empty_references(source: str, article: str, status: str, note: str,
                      include_mermaid: bool = False) -> dict:
    """조문을 찾지 못했을 때의 정상 응답.

    "없음"은 오류가 아니라 결과이므로, 성공 응답과 같은 키 구성을 유지한 채
    status로만 구분한다. 소비자(공개 게이트웨이 포함)가 응답 형태를 바꾸지
    않고 그대로 처리할 수 있게 하기 위함이다.
    """
    result = {
        "target": {
            "source": source,
            "article": article,
            "article_title": None,
            "citation": f"{source} {article}".strip(),
        },
        "outgoing": [],
        "incoming": [],
        "counts": {"outgoing": 0, "incoming": 0},
        "status": status,
        "note": note,
    }
    if include_mermaid:
        result["mermaid"] = _mermaid_graph(result)
    return result


def find_references(source: str, article: str, limit: int = 20,
                    include_mermaid: bool = False) -> dict:
    """대상 조문의 정방향(outgoing) · 역방향(incoming) 인용 관계.

    outgoing: 이 조문 본문이 인용한 다른 조문들 (인덱스 매칭 포함).
    incoming: 다른 조문이 이 조문을 인용한 곳.

    각 인용은 scope로 분류:
      - same_regulation: 같은 규정 안
      - cross_regulation: 다른 KOICA 규정/법 (인덱스 매칭됨)
      - external: 인덱스에 없는 외부 법령 (예: 공공재정환수법)

    include_mermaid=True 이면 반환 dict에 "mermaid" 키로 flowchart 코드를 함께
    담는다 (claude.ai 등에서 인용망을 바로 시각화).

    status는 ok / not_found / invalid_article 중 하나. 조문을 찾지 못한 경우도
    오류가 아니라 빈 그래프(outgoing·incoming 0건)로 반환한다.
    """
    parsed = _parse_article_token(article)
    if parsed is None:
        return _empty_references(
            source, article, "invalid_article",
            f"조문 번호를 해석할 수 없습니다: {article!r}",
            include_mermaid=include_mermaid,
        )
    no, sub = parsed
    articles = load_index()

    src_ok = _source_selector(source, articles)
    targets = [
        a for a in articles
        if src_ok(a.source) and a.article_no == no and a.article_sub == sub
    ]
    if not targets:
        cite = f"제{no}조" + (f"의{sub}" if sub else "")
        return _empty_references(
            source, cite, "not_found",
            f"인덱스에 없는 조문입니다: {source} {cite}",
            include_mermaid=include_mermaid,
        )

    # 본칙(비-부칙) 조문을 target으로 우선 — 부칙 제N조 오선택 방지
    targets.sort(key=lambda a: a.is_supplementary)
    target = targets[0]
    target_source = target.source
    target_art = target.article

    known_sources = sorted({a.source for a in articles}, key=len, reverse=True)

    # ===== OUTGOING =====
    outgoing: list[dict] = []
    seen: set[tuple] = set()
    consumed_spans: list[tuple[int, int]] = []

    for m in _EXTERNAL_CITE_RE.finditer(target.body):
        cited_name = re.sub(r"\s+", "", (m.group(1) or m.group(2) or ""))
        c_no = int(m.group(3))
        c_sub = int(m.group(4) or 0)
        key = (cited_name, c_no, c_sub)
        if key in seen:
            continue
        seen.add(key)
        consumed_spans.append((m.start(), m.end()))
        matched_source = next((s for s in known_sources if cited_name in s or s in cited_name), None)
        if matched_source:
            cited = next(
                (a for a in articles if a.source == matched_source
                 and a.article_no == c_no and a.article_sub == c_sub),
                None,
            )
            if cited:
                outgoing.append({
                    "scope": "cross_regulation",
                    "citation": f"{cited.source} {cited.article}",
                    "article_title": cited.article_title,
                    "snippet": _strip_meta(cited.body[:200].replace("\n", " "))[:120],
                })
                continue
        outgoing.append({
            "scope": "external",
            "citation": f"{cited_name} 제{c_no}조" + (f"의{c_sub}" if c_sub else ""),
            "note": "인덱스에 없는 외부 법령 또는 매칭 실패",
        })

    # 같은 규정 안의 단순 "제N조" 인용 (외부 인용 위치는 스킵)
    for m in _SAME_REG_CITE_RE.finditer(target.body):
        if any(s <= m.start() < e for s, e in consumed_spans):
            continue
        c_no = int(m.group(1))
        c_sub = int(m.group(2) or 0)
        if (c_no, c_sub) == (no, sub):
            continue
        key = (target_source, c_no, c_sub)
        if key in seen:
            continue
        seen.add(key)
        cited = next(
            (a for a in articles if a.source == target_source
             and a.article_no == c_no and a.article_sub == c_sub),
            None,
        )
        if cited:
            outgoing.append({
                "scope": "same_regulation",
                "citation": f"{target_source} {cited.article}",
                "article_title": cited.article_title,
                "snippet": _strip_meta(cited.body[:200].replace("\n", " "))[:120],
            })

    # ===== INCOMING =====
    incoming: list[dict] = []
    for a in articles:
        if a is target:
            continue
        if a.source == target_source:
            # 같은 규정 내 단순 인용
            for m in _SAME_REG_CITE_RE.finditer(a.body):
                if int(m.group(1)) == no and int(m.group(2) or 0) == sub:
                    incoming.append({
                        "scope": "same_regulation",
                        "citation": f"{a.source} {a.article}",
                        "article_title": a.article_title,
                        "snippet": _around(a.body, m.start()),
                    })
                    break
        else:
            # 외부 규정이 이 조문을 인용?
            if target_source not in a.body:
                continue
            pos = 0
            while True:
                idx = a.body.find(target_source, pos)
                if idx < 0:
                    break
                after = a.body[idx + len(target_source): idx + len(target_source) + 60]
                m = re.match(r"\s*제(\d+)조(?:의(\d+))?", after)
                if m and int(m.group(1)) == no and int(m.group(2) or 0) == sub:
                    incoming.append({
                        "scope": "cross_regulation",
                        "citation": f"{a.source} {a.article}",
                        "article_title": a.article_title,
                        "snippet": _around(a.body, idx),
                    })
                    break
                pos = idx + len(target_source)

    result = {
        "target": {
            "source": target.source,
            "article": target.article,
            "article_title": target.article_title,
            "citation": f"{target_source} {target_art}",
        },
        "outgoing": outgoing[:limit],
        "incoming": incoming[:limit],
        "counts": {"outgoing": len(outgoing), "incoming": len(incoming)},
        "status": "ok",
    }
    if include_mermaid:
        result["mermaid"] = _mermaid_graph(result)
    return result


def _mermaid_graph(result: dict) -> str:
    """find_references 결과를 mermaid flowchart 코드로. incoming→target→outgoing."""
    lines = ["flowchart LR"]
    ids: dict[str, str] = {}

    def node(label: str, style: str = "") -> str:
        if label not in ids:
            ids[label] = f"n{len(ids)}"
            safe = label.replace('"', "'").replace("[", "(").replace("]", ")")
            lines.append(f'  {ids[label]}["{safe}"]{style}')
        return ids[label]

    tgt = result["target"]["citation"]
    tgt_id = node(tgt)
    lines.append(f"  style {tgt_id} fill:#dbeafe,stroke:#2563eb")
    for i in result["incoming"]:
        lines.append(f"  {node(i['citation'])} --> {tgt_id}")
    for o in result["outgoing"]:
        lines.append(f"  {tgt_id} --> {node(o['citation'])}")
    return "\n".join(lines)


def _around(body: str, pos: int, span: int = 60) -> str:
    start = max(0, pos - span)
    end = min(len(body), pos + span)
    s = _strip_meta(body[start:end].replace("\n", " "))
    return ("…" if start > 0 else "") + s + ("…" if end < len(body) else "")


# ── 규정 정비 레이더 (하위 규정 vs 모규정 개정 대조) ────────────────────
_RADAR_DATE_RE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})")
_PARENT_CITE_RE = re.compile(r"「([^」]{2,40}?)」")
_CHILD_TYPES = {"시행세칙", "세칙", "지침"}
_PARENT_TYPES = {"규정", "정관"}


def _revision_date(revision: str) -> Optional[tuple]:
    m = _RADAR_DATE_RE.search(revision or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _parent_candidates(by_source: dict, cat_of: dict, child: str) -> dict:
    """모규정 후보(규정/정관 유형)의 {정규화명: 원본명} 맵."""
    return {_normalize_source(s): s for s in by_source
            if s != child and cat_of.get(s) in _PARENT_TYPES}


def _guess_parent(child: str, by_source: dict, cat_of: dict) -> Optional[str]:
    """시행세칙/지침의 모(母)규정명 추론 — 이름 규칙 우선, 실패 시 제1조 「」 인용.

    비교는 모두 `_normalize_source`(공백·중점·연결어 제거)로 수행해, 문서마다 다른
    공백/유니코드 중점 표기 차이로 모규정을 놓치던 미탐을 없앤다.
    """
    cands = _parent_candidates(by_source, cat_of, child)
    # 1) 이름 규칙: "X 시행세칙"/"X 세칙" → X (정규화 일치하는 모규정)
    for suf in (" 시행세칙", " 세칙"):
        if child.endswith(suf):
            key = _normalize_source(child[: -len(suf)])
            if key in cands:
                return cands[key]
    # 2) 제1조(목적, 본칙) 본문의 「규정명」 인용을 정규화 매칭
    arts = by_source.get(child, [])
    first = next((a for a in arts if a.article_no == 1 and a.article_sub == 0
                  and not a.is_supplementary), None)
    if first is None:
        first = next((a for a in arts if a.article_no == 1 and a.article_sub == 0),
                     arts[0] if arts else None)
    if first:
        for m in _PARENT_CITE_RE.finditer(first.body):
            nk = _normalize_source(m.group(1))
            if nk in cands:                       # 정확(정규화) 일치 우선
                return cands[nk]
            partial = [orig for k, orig in cands.items() if nk and (nk in k or k in nk)]
            if partial:
                return max(partial, key=len)      # 최장 후보 (first-match-wins 위험 완화)
    return None


def compliance_radar(source: Optional[str] = None) -> list[dict]:
    """시행세칙·지침이 모(母)규정 개정에 뒤처졌는지 자동 점검(정비 레이더).

    각 하위 규정의 모규정을 이름 규칙/제1조 인용으로 추론하고, 모규정 개정일이
    하위 규정 개정일보다 최근이면 'review_needed'(정비 검토 대상)로 플래그한다.
    한국 조례 정비 관행(상위법 개정 추적)을 KOICA 규정 체계에 옮긴 것.

    Args:
        source: 특정 규정만 점검(부분일치). 없으면 전체에서 정비 필요 목록 반환.

    Returns:
        [{source, type, revision, parent, parent_revision, status, note}, …]
        status: review_needed(모규정이 더 최근) / ok / unknown(개정일 파싱 불가)
    """
    articles = load_index()
    by_source: dict[str, list] = {}
    for a in articles:
        by_source.setdefault(a.source, []).append(a)
    cat_of = {s: arts[0].category for s, arts in by_source.items()}
    rev_of = {s: arts[0].revision for s, arts in by_source.items()}

    out = []
    for src in by_source:
        if cat_of[src] not in _CHILD_TYPES:
            continue
        parent = _guess_parent(src, by_source, cat_of)
        # source 필터: 하위규정명 또는 모규정명에 매칭 (모규정 기준 조회 지원)
        if source and not (source_match(source, src)
                           or (parent and source_match(source, parent))):
            continue
        if not parent:
            # 모규정 추론 실패 — 조용히 스킵하지 않고 no_parent로 노출(점검 불가 투명화)
            out.append({
                "source": src, "type": cat_of[src], "revision": rev_of[src],
                "parent": None, "parent_revision": None, "status": "no_parent",
                "note": "이름 규칙/제1조 인용에서 인덱스 내 모규정을 찾지 못함 — 점검 불가",
            })
            continue
        cd, pd = _revision_date(rev_of[src]), _revision_date(rev_of[parent])
        entry = {
            "source": src, "type": cat_of[src], "revision": rev_of[src],
            "parent": parent, "parent_revision": rev_of[parent],
        }
        if cd and pd and pd > cd:
            gap = (pd[0] - cd[0]) * 12 + (pd[1] - cd[1])
            entry["status"] = "review_needed"
            entry["note"] = f"모규정이 약 {max(gap, 1)}개월 뒤 개정됨 → 정비 검토 대상"
        elif cd and pd:
            entry["status"] = "ok"
            entry["note"] = "모규정 개정 시점까지 반영됨"
        else:
            entry["status"] = "unknown"
            entry["note"] = "개정일 파싱 불가"
        out.append(entry)
    _order = {"review_needed": 0, "unknown": 1, "ok": 2, "no_parent": 3}
    out.sort(key=lambda x: (_order.get(x["status"], 9), x["source"]))
    return out


_QUESTIONS_CACHE: Optional[list[dict]] = None


def _load_questions() -> list[dict]:
    global _QUESTIONS_CACHE
    if _QUESTIONS_CACHE is None:
        path = ROOT / "data" / "questions.json"
        _QUESTIONS_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _QUESTIONS_CACHE


_QUESTION_REF_RE = re.compile(r"근거:\s*([^제\n]+?)\s*(제\d+조(?:의\d+)?)")


def find_questions(
    query: Optional[str] = None,
    question_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """questions.json에서 시험 문항 검색 + 근거 조문 자동 매핑."""
    qs = _load_questions()

    if question_id:
        matched = [q for q in qs if q.get("id") == question_id]
    else:
        if not query:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scored = []
        for q in qs:
            blob = _nfc(
                q.get("question", "")
                + " " + " ".join(q.get("options", []))
                + " " + q.get("explanation", "")
            )
            sc = sum(blob.count(t) for t in tokens)
            if sc > 0:
                scored.append((sc, q))
        scored.sort(key=lambda x: x[0], reverse=True)
        matched = [q for _, q in scored[:limit]]

    out = []
    for q in matched[:limit]:
        ref = None
        m = _QUESTION_REF_RE.search(q.get("explanation", ""))
        if m:
            arts = get_article(m.group(1).strip(), m.group(2))
            if arts:
                ref = {
                    "citation": f"{arts[0]['source']} {m.group(2)}",
                    "article_title": arts[0]["article_title"],
                    "body_excerpt": _strip_meta(arts[0]["body"][:350].replace("\n", " "))[:200],
                }
            else:
                ref = {
                    "citation": f"{m.group(1).strip()} {m.group(2)}",
                    "article_title": None,
                    "body_excerpt": None,
                    "note": "근거 조문이 인덱스에 없음",
                }
        out.append({
            "id": q.get("id"),
            "category": q.get("category"),
            "source": q.get("source"),
            "question": q.get("question"),
            "options": q.get("options"),
            "answer": q.get("answer"),
            "explanation": q.get("explanation"),
            "reference": ref,
        })
    return out


def list_attachments(
    source: Optional[str] = None,
    category: Optional[str] = None,
    kind: Optional[str] = None,
    include_deleted: bool = False,
) -> list[dict]:
    """별표·별지 목록을 source/category/kind로 필터링하여 반환."""
    atts = load_attachments()
    out = []
    for a in atts:
        if category and a.category != category:
            continue
        if source and not source_match(source, a.source):
            continue
        if kind and a.kind != kind:
            continue
        if not include_deleted and a.deleted:
            continue
        out.append({
            "category": a.category,
            "source": a.source,
            "kind": a.kind,
            "label": a.label,
            "title": a.title,
            "deleted": a.deleted,
            "citation": a.citation,
            "body_excerpt": _strip_meta(a.body[:200].replace("\n", " "))[:150] if a.body else "",
        })
    out.sort(key=lambda x: (x["source"], x["kind"], x["label"]))
    return out


def _att_num_key(n: str) -> str:
    """별표·별지 번호 비교 키 — 공백·'서식' 제거. '별표 1'≠'별표 10' 경계 구분용."""
    return re.sub(r"\s+", "", _nfc(n)).replace("서식", "")


def get_attachment(source: str, label: str) -> list[dict]:
    """source 부분일치 + label **경계 있는 정확매칭**으로 별표·별지 본문 반환.

    label은 "[별표 1]", "별표 1", "별지 제3호 서식" 등 자유 형식. 번호는 정확일치라
    "별표 1"이 별표 10·11·1-1을 잡던 과다매칭을 없앴다(종류(별표/별지)도 함께 대조).
    """
    atts = load_attachments()
    src_ok = _source_selector(source, atts)   # 규정명 정확일치 우선 (형제 규정 흡수 방지)
    q = re.sub(r"[\[\]]", "", _nfc(label)).strip()
    mk = re.match(r"^\s*(별표|별지)?\s*(.*)$", q)
    q_kind = (mk.group(1) or "") if mk else ""
    q_num = _att_num_key(mk.group(2) if mk else q)
    out = []
    for a in atts:
        if not src_ok(a.source):
            continue
        if q_kind and a.kind != q_kind:
            continue
        if q_num and _att_num_key(a.number) == q_num:
            out.append({
                "category": a.category,
                "source": a.source,
                "revision": a.revision,
                "kind": a.kind,
                "label": a.label,
                "title": a.title,
                "deleted": a.deleted,
                "citation": a.citation,
                "body": a.body,
            })
    return out


def _restart_instruction() -> dict:
    """현재 OS에 맞는 Claude Desktop 재시작 안내."""
    p = platform.system()
    if p == "Darwin":
        return {
            "os": "macOS",
            "instruction": "Claude Desktop을 완전 종료(Cmd+Q 또는 메뉴바 → Quit) 후 다시 실행해 주세요.",
        }
    if p == "Windows":
        return {
            "os": "Windows",
            "instruction": "Claude Desktop을 완전 종료 후 다시 실행해 주세요. (시스템 트레이의 Claude 아이콘 우클릭 → Quit, 또는 작업관리자에서 Claude 프로세스 종료)",
        }
    return {
        "os": p,
        "instruction": "Claude Desktop을 완전 종료한 뒤 다시 실행해 주세요.",
    }


def self_update() -> dict:
    """저장소를 최신으로 갱신(git pull)하고 인덱스를 재빌드.

    Claude Desktop 등 MCP 클라이언트에서 자연어로 "도구 업데이트" 호출 시 사용.
    코드 파일이 바뀐 경우 클라이언트 재시작이 필요하므로, OS에 맞는 재시작 안내를
    함께 반환한다.
    """
    result: dict = {"steps": []}

    # 1) git pull --ff-only (충돌 방지)
    try:
        r = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return {"status": "error", "message": "git 명령을 찾을 수 없습니다. PATH에 git이 있는지 확인하세요."}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "git pull이 60초 안에 끝나지 않았습니다. 네트워크를 확인하세요."}

    git_output = (r.stdout + "\n" + r.stderr).strip()
    result["steps"].append({"step": "git pull", "returncode": r.returncode, "output": git_output})

    if r.returncode != 0:
        return {
            **result,
            "status": "error",
            "message": "git pull 실패. 충돌이나 인증 문제일 수 있습니다.",
        }

    already_up_to_date = (
        "Already up to date" in git_output
        or "Already up-to-date" in git_output
        or "이미 최신" in git_output
    )
    if already_up_to_date:
        return {
            **result,
            "status": "no_change",
            "message": "이미 최신 상태입니다. 업데이트할 내용이 없습니다.",
            "restart_required": False,
        }

    # 변경된 파일 추출 (.py 파일이 바뀌었으면 재시작 필수)
    changed_files: list[str] = []
    for line in git_output.splitlines():
        m = re.match(r"^\s*([^\s|]+\.(?:py|md|json|txt))\s*\|", line)
        if m:
            changed_files.append(m.group(1))
    code_changed = any(f.endswith(".py") for f in changed_files)
    data_changed = any("data/" in f or f.endswith(".md") for f in changed_files)

    # 의미 검색은 선택 의존성을 지연 로드한다. requirements가 갱신된 경우에만
    # 현재 서버와 같은 Python 환경에 설치해 다음 빌드가 dense artifact도 만들게 한다.
    if "requirements.txt" in changed_files:
        try:
            dependency_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            return {
                **result,
                "status": "error",
                "message": "검색 의존성 설치가 10분 안에 끝나지 않았습니다.",
            }
        result["steps"].append({
            "step": "install dependencies",
            "returncode": dependency_result.returncode,
            "output": (dependency_result.stdout + "\n" + dependency_result.stderr).strip(),
        })
        if dependency_result.returncode != 0:
            return {
                **result,
                "status": "error",
                "message": "검색 의존성 설치에 실패했습니다.",
            }

    # 2) build
    prev_count = 0
    if INDEX_PATH.exists():
        try:
            raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            # v2: {"articles": [...]}, v1(호환): [...]
            prev_count = len(raw["articles"]) if isinstance(raw, dict) else len(raw)
        except Exception:
            pass
    previous_threads = os.environ.get("KOICA_SEMANTIC_THREADS")
    os.environ["KOICA_SEMANTIC_THREADS"] = "4"
    try:
        try:
            import semantic_search

            semantic_search.reset_caches()
        except Exception:
            pass
        articles, attachments = build_index()
    except Exception as e:
        return {
            **result,
            "status": "error",
            "message": f"인덱스 빌드 실패: {e}",
        }
    finally:
        if previous_threads is None:
            os.environ.pop("KOICA_SEMANTIC_THREADS", None)
        else:
            os.environ["KOICA_SEMANTIC_THREADS"] = previous_threads
        try:
            import semantic_search

            semantic_search.reset_caches()
        except Exception:
            pass

    global _INDEX_CACHE, _ATTACHMENT_CACHE
    _INDEX_CACHE = None
    _ATTACHMENT_CACHE = None

    result["steps"].append({
        "step": "build",
        "article_count_before": prev_count,
        "article_count_after": len(articles),
        "delta": len(articles) - prev_count,
    })

    restart = _restart_instruction()
    return {
        **result,
        "status": "ok",
        "changed_files": changed_files,
        "code_changed": code_changed,
        "data_changed": data_changed,
        "restart_required": code_changed,
        "restart_instruction": restart["instruction"],
        "detected_os": restart["os"],
        "message": (
            f"최신 코드와 인덱스를 받았습니다. {restart['instruction']}"
            if code_changed
            else "데이터만 갱신되었습니다. Claude Desktop 재시작 없이 즉시 사용할 수 있습니다."
        ),
    }


def cmd_build(args: argparse.Namespace) -> None:
    if args.no_semantic and args.strict_semantic:
        raise ValueError("--no-semantic과 --strict-semantic은 함께 사용할 수 없습니다.")
    os.environ.setdefault("KOICA_SEMANTIC_THREADS", "4")
    build_index(
        build_semantic=not args.no_semantic,
        strict_semantic=args.strict_semantic,
    )


def cmd_search(args: argparse.Namespace) -> None:
    results = search(
        args.query,
        args.category,
        args.source,
        args.limit,
        fuzzy=args.fuzzy,
        include_attachments=args.include_attachments,
        mode=args.mode,
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        print("결과 없음")
        return
    for i, r in enumerate(results, 1):
        title = r["article_title"] if r["type"] == "article" else r["title"]
        location = r.get("chapter") or r.get("kind", "")
        print(f"[{i}] {r['citation']}  ({title})  score={r['score']}")
        print(f"    📂 {r['category']} · {location}  · 개정 {r['revision']}")
        print(f"    {r['snippet']}")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="KOICA 규정 조문 검색 (MVP)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="인덱스 빌드 (data/extracted → data/index.json)")
    build_mode = pb.add_mutually_exclusive_group()
    build_mode.add_argument("--no-semantic", action="store_true", help="의미 인덱스 빌드 생략")
    build_mode.add_argument(
        "--strict-semantic",
        action="store_true",
        help="의미 인덱스 빌드 실패 시 전체 빌드 실패 (배포용)",
    )
    pb.set_defaults(func=cmd_build)

    ps = sub.add_parser("search", help="조문 검색")
    ps.add_argument("query")
    ps.add_argument("--category", help="규정 유형: 규정/시행세칙/지침/기준/정관/세칙")
    ps.add_argument("--source", help="규정명 부분일치 (예: 인사규정)")
    ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--fuzzy", action="store_true", help="음절 bi-gram 부분 매칭")
    ps.add_argument("--include-attachments", action="store_true", help="별표·별지도 검색")
    ps.add_argument(
        "--mode",
        choices=sorted(SEARCH_MODES),
        default="hybrid",
        help="검색 방식 (기본: hybrid)",
    )
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_search)

    pg = sub.add_parser("get", help="조문 본문 정확 조회")
    pg.add_argument("source", help="규정명 부분일치")
    pg.add_argument("article", help="조문 번호 (예: 제11조, 15의2)")
    pg.set_defaults(func=lambda a: print(json.dumps(get_article(a.source, a.article), ensure_ascii=False, indent=2)))

    pv = sub.add_parser("verify", help="텍스트의 인용 조문 검증")
    pv.add_argument("text", help="검증할 텍스트 (따옴표로 감싸기)")
    pv.set_defaults(func=lambda a: print(json.dumps(verify_citation(a.text), ensure_ascii=False, indent=2)))

    pa = sub.add_parser("attachments", help="별표·별지 목록")
    pa.add_argument("--source")
    pa.add_argument("--category")
    pa.add_argument("--kind", choices=["별표", "별지"])
    pa.add_argument("--include-deleted", action="store_true")
    pa.set_defaults(func=lambda a: print(json.dumps(list_attachments(a.source, a.category, a.kind, a.include_deleted), ensure_ascii=False, indent=2)))

    pat = sub.add_parser("attachment", help="별표·별지 본문 조회")
    pat.add_argument("source")
    pat.add_argument("label", help="예: '별표 1', '[별지 제3호 서식]'")
    pat.set_defaults(func=lambda a: print(json.dumps(get_attachment(a.source, a.label), ensure_ascii=False, indent=2)))

    pu = sub.add_parser("update", help="저장소 최신 갱신 (git pull + build)")
    pu.set_defaults(func=lambda _a: print(json.dumps(self_update(), ensure_ascii=False, indent=2)))

    pr = sub.add_parser("refs", help="조문 인용 관계 (outgoing/incoming)")
    pr.add_argument("source")
    pr.add_argument("article")
    pr.add_argument("--limit", type=int, default=20)
    pr.add_argument("--mermaid", action="store_true", help="mermaid flowchart 코드 포함")
    pr.set_defaults(func=lambda a: print(json.dumps(find_references(a.source, a.article, a.limit, include_mermaid=a.mermaid), ensure_ascii=False, indent=2)))

    prd = sub.add_parser("radar", help="규정 정비 레이더 (시행세칙·지침 vs 모규정 개정 대조)")
    prd.add_argument("source", nargs="?", help="특정 규정만 (생략 시 전체)")
    prd.set_defaults(func=lambda a: print(json.dumps(compliance_radar(a.source), ensure_ascii=False, indent=2)))

    pq = sub.add_parser("question", help="시험문제 검색")
    pq.add_argument("query", nargs="?")
    pq.add_argument("--id", dest="qid")
    pq.add_argument("--limit", type=int, default=3)
    pq.set_defaults(func=lambda a: print(json.dumps(find_questions(query=a.query, question_id=a.qid, limit=a.limit), ensure_ascii=False, indent=2)))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
