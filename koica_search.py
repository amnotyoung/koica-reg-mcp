"""KOICA 규정 조문 단위 검색 (MVP).

사용:
    python koica_search.py build
    python koica_search.py search "인사위원회"
    python koica_search.py search "공공기관운영위원회 심의" --category law
"""

from __future__ import annotations

import argparse
import json
import math
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
ARTICLE_MD_RE = re.compile(r"^#{2,3} (제(\d+)조(?:의(\d+))?)\s*\((.+?)\)\s*$")
# Format B (PDF 평문): "      제1장 총칙", "제1조(목적) 본문…"
CHAPTER_PLAIN_RE = re.compile(r"^\s*(제\d+(?:편|장|절))\s+(\S.{0,40})\s*$")
ARTICLE_PLAIN_RE = re.compile(r"^\s*제(\d+)조(?:의(\d+))?\s*\(([^)]+)\)\s*(.*)$")

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
    s = re.sub(r"\s+", "", _nfc(s))
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

        # 별표·별지 라인 — 어디서든 등장 가능 (조문 진행 중에도)
        if "[별표" in line or "[별지" in line:
            if absorb_attachment_line(line):
                continue
        # Format A: 마크다운 ## / ### (조문은 ##·### 모두 허용)
        if line.startswith("##"):
            m_art = ARTICLE_MD_RE.match(line)
            if m_art:
                open_article(int(m_art.group(2)), int(m_art.group(3) or 0), m_art.group(4), "")
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


def build_index() -> tuple[list[Article], list[Attachment]]:
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


def search(
    query: str,
    category: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 10,
    fuzzy: bool = False,
    include_attachments: bool = False,
) -> list[dict]:
    articles = load_index()
    tokens = tokenize(query)
    if not tokens:
        return []
    idf = compute_idf(tokens, articles)

    scored: list[tuple] = []
    for a in articles:
        if category and a.category != category:
            continue
        if source and not source_match(source, a.source):
            continue
        sc, pos = score_article(a, tokens, idf, fuzzy=fuzzy)
        if sc <= 0:
            continue
        scored.append((sc, pos, "article", a))

    if include_attachments:
        for a in load_attachments():
            if a.deleted:
                continue
            if category and a.category != category:
                continue
            if source and not source_match(source, a.source):
                continue
            sc = 0.0
            pos = -1
            for tok in tokens:
                w = idf.get(tok, 1.0)
                if tok in a.title:
                    sc += 5.0 * w
                cnt = a.body.count(tok)
                if cnt:
                    sc += float(cnt) * w
                    p = a.body.find(tok)
                    if pos < 0 or p < pos:
                        pos = p
            if sc > 0:
                scored.append((sc, pos, "attachment", a))

    scored.sort(key=lambda r: r[0], reverse=True)
    out = []
    for sc, pos, kind, item in scored[:limit]:
        if kind == "article":
            out.append({
                "type": "article",
                "category": item.category,
                "source": item.source,
                "revision": item.revision,
                "chapter": item.chapter,
                "article": item.article,
                "article_title": item.article_title,
                "citation": item.citation,
                "snippet": make_snippet(item.body, pos),
                "score": round(sc, 2),
            })
        else:
            out.append({
                "type": "attachment",
                "category": item.category,
                "source": item.source,
                "revision": item.revision,
                "kind": item.kind,
                "label": item.label,
                "title": item.title,
                "citation": item.citation,
                "snippet": make_snippet(item.body, pos),
                "score": round(sc, 2),
            })
    return out


ARTICLE_TOKEN_RE = re.compile(r"^\s*(?:제)?(\d+)조?(?:의(\d+))?\s*$")
# 인용: 제N조[의M] [제N항][제N호] 뒤에 (조문제목)이 붙으면 내용검증에 사용
CITATION_RE = re.compile(
    r"제(\d+)조(?:의(\d+))?(?:\s*제\d+항)?(?:\s*제\d+호)?(?:\s*\(([^)]{2,40})\))?"
)


def _title_key(s: str) -> str:
    """제목 비교용 정규화 — 공백·문장부호 제거."""
    return re.sub(r"[\s·․.,'\"()\[\]「」]", "", _nfc(s))


def _title_matches(cited: str, actual: str) -> bool:
    """인용에 붙은 조문제목이 실제 제목과 부합하는지 (관대한 판정).

    정확일치·포함 또는 음절 bigram Jaccard ≥ 0.4 이면 일치로 본다.
    (엉뚱한 제목을 붙인 환각만 걸러내고, 축약·이표기는 통과)
    """
    c, a = _title_key(cited), _title_key(actual)
    if not c or not a:
        return True
    if c == a or c in a or a in c:
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


def get_article(source: str, article: str) -> list[dict]:
    """source 부분일치 + article 정확매칭으로 조문 본문 전체 반환."""
    parsed = _parse_article_token(article)
    if parsed is None:
        return []
    no, sub = parsed
    out = []
    for a in load_index():
        if source and not source_match(source, a.source):
            continue
        if a.article_no == no and a.article_sub == sub:
            out.append({
                "category": a.category,
                "source": a.source,
                "revision": a.revision,
                "chapter": a.chapter,
                "article": a.article,
                "article_title": a.article_title,
                "citation": a.citation,
                "body": a.body,
            })
    return out


def _article_range_for(source_nfc: str, articles: list[Article]) -> str:
    nos = sorted({(a.article_no, a.article_sub) for a in articles if source_nfc in a.source})
    if not nos:
        return "(해당 source 없음)"
    first = f"제{nos[0][0]}조" + (f"의{nos[0][1]}" if nos[0][1] else "")
    last = f"제{nos[-1][0]}조" + (f"의{nos[-1][1]}" if nos[-1][1] else "")
    return f"{first} ~ {last}, 총 {len(nos)}개"


def verify_citation(text: str) -> list[dict]:
    """텍스트 내 모든 '{규정명} 제N조[의M]' 인용을 인덱스로 교차검증.

    각 인용에 대해 status:
      - ok: 규정·조문 실재 (인용에 조문제목이 붙었으면 제목까지 일치)
      - content_mismatch: 조문은 실재하나 붙은 제목이 실제와 다름 (내용 환각)
      - not_found: 규정은 알지만 해당 조문 없음
      - unknown_source: 직전 텍스트에서 알려진 규정명을 못 찾음

    content_mismatch는 "인사규정 제11조(육아휴직)"처럼 존재하는 조문번호에
    엉뚱한 제목을 붙인 LLM 환각을 잡는다.
    """
    text_nfc = _nfc(text)
    articles = load_index()
    known_sources = sorted({a.source for a in articles}, key=len, reverse=True)

    def nearest_source(prefix: str) -> Optional[str]:
        best, best_pos = None, -1
        for src in known_sources:
            pos = prefix.rfind(src)
            # 끝에 가까운 매칭 우선, 동률이면 더 긴 라벨
            if pos > best_pos or (pos == best_pos and best and len(src) > len(best)):
                best_pos, best = pos, src
        return best if best_pos >= 0 else None

    results = []
    for m in CITATION_RE.finditer(text_nfc):
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
        hit = next(
            (a for a in articles
             if a.source == matched_src and a.article_no == no and a.article_sub == sub),
            None,
        )
        if hit:
            cited_title = (m.group(3) or "").strip()
            if cited_title and not _title_matches(cited_title, hit.article_title):
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
                    "title_verified": bool(cited_title),
                    "body_excerpt": _strip_meta(hit.body[:250].replace("\n", " "))[:150],
                })
        else:
            results.append({
                "citation": f"{matched_src} {art}",
                "raw_match": full_cite,
                "status": "not_found",
                "message": f"{matched_src}에 {art} 없음 (실재: {_article_range_for(matched_src, articles)})",
            })
    return results


# 같은 규정 안의 "제N조" 인용 (앞에 규정명이 안 붙은 경우)
_SAME_REG_CITE_RE = re.compile(r"(?<![가-힣A-Za-z\w])제(\d+)조(?:의(\d+))?")
# 외부 규정 인용: "「법령명」 제N조" 또는 "{규정명} 제N조"
_EXTERNAL_CITE_RE = re.compile(
    r"(?:「([^」\n]{2,40}?)」|((?:[가-힣]+\s?){1,6}?(?:규정|법률|법|지침|세칙|정관|매뉴얼)))\s*제(\d+)조(?:의(\d+))?"
)


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
    """
    parsed = _parse_article_token(article)
    if parsed is None:
        return {"error": f"invalid article token: {article!r}"}
    no, sub = parsed
    articles = load_index()

    targets = [
        a for a in articles
        if source_match(source, a.source) and a.article_no == no and a.article_sub == sub
    ]
    if not targets:
        return {"error": f"target not found: {source} 제{no}조" + (f"의{sub}" if sub else "")}

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


def _guess_parent(child: str, by_source: dict, cat_of: dict) -> Optional[str]:
    """시행세칙/지침의 모(母)규정명 추론 — 이름 규칙 우선, 실패 시 제1조 「」 인용."""
    # 1) 이름 규칙: "X 시행세칙"/"X 세칙" → "X" (모규정 유형일 때만)
    for suf in (" 시행세칙", " 세칙"):
        if child.endswith(suf):
            cand = child[: -len(suf)].strip()
            if cat_of.get(cand) in _PARENT_TYPES:
                return cand
    # 2) 제1조(목적) 본문의 「규정명」 인용 중 모규정 유형만 채택
    arts = by_source.get(child, [])
    first = next((a for a in arts if a.article_no == 1 and a.article_sub == 0), arts[0] if arts else None)
    if first:
        for m in _PARENT_CITE_RE.finditer(first.body):
            name = m.group(1).strip()
            if name != child and cat_of.get(name) in _PARENT_TYPES:
                return name
            for src in by_source:
                if src != child and cat_of.get(src) in _PARENT_TYPES and (name in src or src in name):
                    return src
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
        if source and not source_match(source, src):
            continue
        parent = _guess_parent(src, by_source, cat_of)
        if not parent:
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
    out.sort(key=lambda x: (x["status"] != "review_needed", x["source"]))
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


def get_attachment(source: str, label: str) -> list[dict]:
    """source 부분일치 + label 매칭으로 별표·별지 본문 전체 반환.

    label은 "[별표 1]", "별표 1", "1" 등 자유 형식. 공백·괄호 무시 정규화로 매칭.
    """
    src_q = _nfc(source).strip()
    lab_q = re.sub(r"[\[\]\s]+", "", _nfc(label)).lower()  # "별표1", "별지제3호서식" 형태로
    out = []
    for a in load_attachments():
        if not source_match(src_q, a.source):
            continue
        norm_label = re.sub(r"[\[\]\s]+", "", a.label).lower()
        if lab_q in norm_label or norm_label.endswith(lab_q):
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

    # 2) build
    prev_count = 0
    if INDEX_PATH.exists():
        try:
            raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            # v2: {"articles": [...]}, v1(호환): [...]
            prev_count = len(raw["articles"]) if isinstance(raw, dict) else len(raw)
        except Exception:
            pass
    try:
        articles, attachments = build_index()
    except Exception as e:
        return {
            **result,
            "status": "error",
            "message": f"인덱스 빌드 실패: {e}",
        }

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


def cmd_build(_args: argparse.Namespace) -> None:
    build_index()


def cmd_search(args: argparse.Namespace) -> None:
    results = search(args.query, args.category, args.source, args.limit, fuzzy=args.fuzzy)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if not results:
        print("결과 없음")
        return
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r['citation']}  ({r['article_title']})  score={r['score']}")
        print(f"    📂 {r['category']} · {r['chapter']}  · 개정 {r['revision']}")
        print(f"    {r['snippet']}")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="KOICA 규정 조문 검색 (MVP)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="인덱스 빌드 (data/extracted → data/index.json)")
    pb.set_defaults(func=cmd_build)

    ps = sub.add_parser("search", help="조문 검색")
    ps.add_argument("query")
    ps.add_argument("--category", help="law/hr/project/volunteer/partnership/finance/management")
    ps.add_argument("--source", help="규정명 부분일치 (예: 인사규정)")
    ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--fuzzy", action="store_true", help="음절 bi-gram 부분 매칭")
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
