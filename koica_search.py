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

HEADER_RE = re.compile(r"^# (.+?)(?:\s*\((.+?)\))?\s*$")
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


def parse_md(path: Path, category: str) -> list[Article]:
    text = _nfc(path.read_text(encoding="utf-8"))
    lines = text.splitlines()

    source = _nfc(path.stem)
    revision = ""
    chapter = ""
    articles: list[Article] = []
    cur: Optional[dict] = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal cur, body_lines
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

    for line in lines:
        # 헤더 (문서 첫 줄)
        if line.startswith("# "):
            m = HEADER_RE.match(line)
            if m:
                source = m.group(1).strip()
                revision = (m.group(2) or "").strip()
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
        if cur is not None:
            body_lines.append(line.strip())

    flush()
    return articles


def build_index() -> list[Article]:
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"data 폴더 없음: {DATA_DIR}")
    articles: list[Article] = []
    skipped = []
    for md in sorted(DATA_DIR.glob("*.md")):
        if md.name in CATEGORY_AGGREGATE_FILES:
            skipped.append(md.name)
            continue
        category = md.stem.split("_", 1)[0]
        parsed = parse_md(md, category)
        articles.extend(parsed)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps([asdict(a) for a in articles], ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"빌드: {len(articles)}개 조문 / 합본 스킵 {len(skipped)}개 → {INDEX_PATH}", file=sys.stderr)
    return articles


_INDEX_CACHE: Optional[list[Article]] = None


def load_index(use_cache: bool = True) -> list[Article]:
    global _INDEX_CACHE
    if use_cache and _INDEX_CACHE is not None:
        return _INDEX_CACHE
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"인덱스 없음. 먼저 'build' 실행: {INDEX_PATH}")
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    arts = [Article(**r) for r in raw]
    if use_cache:
        _INDEX_CACHE = arts
    return arts


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
) -> list[dict]:
    articles = load_index()
    tokens = tokenize(query)
    if not tokens:
        return []
    idf = compute_idf(tokens, articles)

    scored = []
    for a in articles:
        if category and a.category != category:
            continue
        if source and not source_match(source, a.source):
            continue
        sc, pos = score_article(a, tokens, idf, fuzzy=fuzzy)
        if sc <= 0:
            continue
        scored.append((sc, pos, a))

    scored.sort(key=lambda r: r[0], reverse=True)
    out = []
    for sc, pos, a in scored[:limit]:
        out.append({
            "category": a.category,
            "source": a.source,
            "revision": a.revision,
            "chapter": a.chapter,
            "article": a.article,
            "article_title": a.article_title,
            "citation": a.citation,
            "snippet": make_snippet(a.body, pos),
            "score": round(sc, 2),
        })
    return out


ARTICLE_TOKEN_RE = re.compile(r"^\s*(?:제)?(\d+)조?(?:의(\d+))?\s*$")
CITATION_RE = re.compile(r"제(\d+)조(?:의(\d+))?(?:\s*제\d+항)?(?:\s*제\d+호)?")


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

    각 인용에 대해 status: ok / not_found / unknown_source.
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
            results.append({
                "citation": f"{matched_src} {art}",
                "raw_match": full_cite,
                "status": "ok",
                "article_title": hit.article_title,
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


def find_references(source: str, article: str, limit: int = 20) -> dict:
    """대상 조문의 정방향(outgoing) · 역방향(incoming) 인용 관계.

    outgoing: 이 조문 본문이 인용한 다른 조문들 (인덱스 매칭 포함).
    incoming: 다른 조문이 이 조문을 인용한 곳.

    각 인용은 scope로 분류:
      - same_regulation: 같은 규정 안
      - cross_regulation: 다른 KOICA 규정/법 (인덱스 매칭됨)
      - external: 인덱스에 없는 외부 법령 (예: 공공재정환수법)
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

    return {
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


def _around(body: str, pos: int, span: int = 60) -> str:
    start = max(0, pos - span)
    end = min(len(body), pos + span)
    s = _strip_meta(body[start:end].replace("\n", " "))
    return ("…" if start > 0 else "") + s + ("…" if end < len(body) else "")


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
            prev_count = len(json.loads(INDEX_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    try:
        articles = build_index()
    except Exception as e:
        return {
            **result,
            "status": "error",
            "message": f"인덱스 빌드 실패: {e}",
        }

    global _INDEX_CACHE
    _INDEX_CACHE = None  # 다음 호출에서 새 인덱스 로드

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

    pu = sub.add_parser("update", help="저장소 최신 갱신 (git pull + build)")
    pu.set_defaults(func=lambda _a: print(json.dumps(self_update(), ensure_ascii=False, indent=2)))

    pr = sub.add_parser("refs", help="조문 인용 관계 (outgoing/incoming)")
    pr.add_argument("source")
    pr.add_argument("article")
    pr.add_argument("--limit", type=int, default=20)
    pr.set_defaults(func=lambda a: print(json.dumps(find_references(a.source, a.article, a.limit), ensure_ascii=False, indent=2)))

    pq = sub.add_parser("question", help="시험문제 검색")
    pq.add_argument("query", nargs="?")
    pq.add_argument("--id", dest="qid")
    pq.add_argument("--limit", type=int, default=3)
    pq.set_defaults(func=lambda a: print(json.dumps(find_questions(query=a.query, question_id=a.qid, limit=a.limit), ensure_ascii=False, indent=2)))

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
