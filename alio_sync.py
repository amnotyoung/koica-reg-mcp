"""ALIO 동기화 — KOICA 현행 규정을 alio.go.kr에서 자동 수집·추출·인덱싱.

파이프라인:
    1) ALIO 규정 목록 조회 (itemReportListSusi.json, 페이지네이션)
    2) 각 규정 상세(itemBoard)에서 현행본(최신 개정본) fileNo·개정일 해석
    3) 현행본 HWP 다운로드 (rulefiledown.json)
    4) kordoc CLI로 HWP → Markdown 일괄 변환
    5) 기존 파서(koica_search)가 먹는 Format A로 정규화
    6) data/extracted/{유형}_{규정명}.md 기록 + sources.json 매니페스트
    7) 인덱스 재빌드 (data/index.json)

사용:
    python3 alio_sync.py            # 전체 동기화 (캐시 활용)
    python3 alio_sync.py --fresh    # 캐시 무시하고 처음부터
    python3 alio_sync.py --no-build # 인덱스 재빌드 생략

요구사항: Node.js + npx (kordoc 실행). 조회 자체에는 불필요, 동기화에만 필요.

외부 법령(공공기관운영법 등)은 ALIO 규정 목록에 없으므로 이 도구가 다루지 않는다.
외부 법령은 korean-law-mcp(https://github.com/chrisryugj/korean-law-mcp)를 함께 사용.
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
EXTRACT_DIR = DATA_DIR / "extracted"
CACHE_DIR = DATA_DIR / "_cache"
HWP_CACHE = CACHE_DIR / "hwp"
MD_CACHE = CACHE_DIR / "md_raw"
SOURCES_PATH = DATA_DIR / "sources.json"

BASE = "https://www.alio.go.kr"
APBA_ID = "C0146"                 # 한국국제협력단
REPORT_ROOT = "21110"            # 규정/사규
LIST_REFERER = f"{BASE}/item/itemOrganList.do?apbaId={APBA_ID}&reportFormRootNo={REPORT_ROOT}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# kordoc 버전 고정 — 환경 간(로컬/CI) 추출 결과 재현성 확보(버전 드리프트로 인한
# 불필요한 diff 방지). 새 버전 반영 시 이 값을 올리고 한 번 재동기화한다.
KORDOC_VERSION = "3.17.0"

# 규정 유형 — 이름 끝 접미사로 판정 (더 구체적인 것 먼저)
TYPE_SUFFIXES = ["시행세칙", "세칙", "규정", "지침", "기준", "정관", "규칙", "매뉴얼"]

FILE_LINK_RE = re.compile(
    r'href="/download/rulefiledown\.json\?fileNo=(\d+)"[^>]*>'
    r'([^<]+\.(?:hwp|hwpx|pdf|docx?|xlsx?|zip))', re.I
)


# ── 유틸 ────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def classify_type(title: str) -> str:
    t = title.strip()
    for suf in TYPE_SUFFIXES:
        if t.endswith(suf):
            return suf
    return "기타"


def safe_filename(title: str) -> str:
    """파일명 안전화: 경로 구분자·제어문자 제거, 공백 정리."""
    s = title.replace("/", "-").replace("\\", "-").replace(":", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def http_get(url: str, referer: str | None = None, binary: bool = False, retries: int = 4):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                return data if binary else data.decode("utf-8", "replace"), r.headers
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET 실패 ({retries}회): {url} — {last}")


def http_post_json(path: str, payload: dict, referer: str | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ── 1) 규정 목록 ────────────────────────────────────────────────────────
def fetch_regulation_list() -> list[dict]:
    """ALIO에서 KOICA 규정 전체 목록(페이지네이션)."""
    apba_type = _resolve_apba_type()
    items: list[dict] = []
    page = 1
    while True:
        payload = {
            "pageNo": page, "apbaId": APBA_ID, "apbaType": apba_type,
            "reportFormRootNo": REPORT_ROOT, "search_word": "", "search_flag": "",
            "bid_type": "", "enfc_istt": "",
        }
        data = http_post_json("/item/itemReportListSusi.json", payload, LIST_REFERER)["data"]
        res = data.get("result", [])
        items.extend(res)
        total_page = data["page"]["totalPage"]
        log(f"  목록 {page}/{total_page}페이지: +{len(res)} (누적 {len(items)})")
        if page >= total_page:
            break
        page += 1
        time.sleep(0.25)
    return items


def _resolve_apba_type() -> str:
    data = http_post_json(
        "/item/itemOrganListSusi.json",
        {"apbaId": APBA_ID, "reportFormRootNo": REPORT_ROOT, "area": []},
        LIST_REFERER,
    )["data"]
    for o in data.get("organList", []):
        if o.get("apbaId") == APBA_ID:
            return o.get("apbaType", "")
    return ""


# ── 2) 현행본 해석 ──────────────────────────────────────────────────────
def _board_url(it: dict) -> str:
    q = urllib.parse.urlencode({
        "disclosureNo": it.get("disclosureNo") or "", "apbaId": it["apbaId"],
        "nowcode": it["reportFormNo"], "reportFormNo": it["reportFormNo"],
        "table_name": it["tableName"], "idx_name": it["idxName"], "idx": it["idx"],
        "reportGbn": "Y", "bid_type": it.get("bidType") or "",
    })
    return f"{BASE}/item/itemBoard{it['reportFormNo']}.do?{q}"


def resolve_current_files(items: list[dict]) -> list[dict]:
    """각 규정 상세에서 현행본(파일 목록 마지막 = 최신 개정본)을 해석."""
    resolved: list[dict] = []
    for i, it in enumerate(items, 1):
        title = it["title"]
        url = _board_url(it)
        try:
            html, _ = http_get(url, referer=LIST_REFERER)
        except Exception as e:  # noqa: BLE001
            log(f"  [{i:3}/{len(items)}] 상세 실패: {title} — {e}")
            continue
        files = [(m.group(1), htmllib.unescape(m.group(2)).strip())
                 for m in FILE_LINK_RE.finditer(html)]
        if not files:
            log(f"  [{i:3}/{len(items)}] 파일 없음: {title}")
            continue
        file_no, file_name = files[-1]   # 마지막 = 현행본
        resolved.append({
            "title": title,
            "type": classify_type(title),
            "file_no": file_no,
            "file_name": file_name,
            "revision": (it.get("stDate") or "").replace(".", "."),  # 제·개정일 (YYYY.MM.DD)
            "idx": it.get("idx"),
            "ext": os.path.splitext(file_name)[1].lower().lstrip("."),
        })
        time.sleep(0.2)
    return resolved


# ── 3) 다운로드 ─────────────────────────────────────────────────────────
def download_files(resolved: list[dict]) -> None:
    HWP_CACHE.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(resolved, 1):
        dest = HWP_CACHE / f"{r['file_no']}.{r['ext'] or 'hwp'}"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        url = f"{BASE}/download/rulefiledown.json?fileNo={r['file_no']}"
        try:
            data = http_get(url, referer=LIST_REFERER, binary=True)
            dest.write_bytes(data)
            log(f"  [{i:3}/{len(resolved)}] ↓ {dest.name} ({len(data)}B) {r['title']}")
        except Exception as e:  # noqa: BLE001
            log(f"  [{i:3}/{len(resolved)}] 다운로드 실패: {r['title']} — {e}")
        time.sleep(0.2)


# ── 4) kordoc 변환 ──────────────────────────────────────────────────────
def kordoc_convert(resolved: list[dict], fresh: bool = False) -> None:
    """캐시된 HWP를 kordoc CLI로 Markdown 변환 (md_raw 캐시)."""
    MD_CACHE.mkdir(parents=True, exist_ok=True)
    todo = []
    for r in resolved:
        src = HWP_CACHE / f"{r['file_no']}.{r['ext'] or 'hwp'}"
        out = MD_CACHE / f"{r['file_no']}.md"
        if not src.exists():
            continue
        if out.exists() and out.stat().st_size > 0 and not fresh:
            continue
        todo.append(str(src))
    if not todo:
        log("  kordoc: 변환할 파일 없음 (전부 캐시됨)")
        return
    log(f"  kordoc: {len(todo)}개 변환 시작 (npx kordoc)…")
    cmd = ["npx", "-y", "-p", f"kordoc@{KORDOC_VERSION}", "-p", "pdfjs-dist",
           "kordoc", *todo, "-d", str(MD_CACHE), "--silent"]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"  kordoc 경고 (returncode={proc.returncode}): {proc.stderr[-500:]}")


# ── 5) 정규화 (kordoc md → Format A) ───────────────────────────────────
_CHAPTER_RE = re.compile(r"^#{1,4}\s+(제\d+(?:편|장|절)\b.*)$")
_ARTICLE_RE = re.compile(r"^#{1,4}\s+(제\d+조(?:의\d+)?\s*\(.+)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def normalize_markdown(md: str, title: str, revision: str) -> str:
    """kordoc 출력을 koica_search.parse_md가 먹는 형식으로 정규화.

    핵심: 정본 제목 헤더('# {규정명} ({개정일} 개정)')를 **항상 맨 위에** 두고,
    그 외 모든 헤딩을 중립화해 source 하이재킹·오(誤)장(章) 인식을 막는다.
    파서(parse_md)는 '# '→source, '## 제N장'→chapter, '#{2,3} 제N조'→article,
    '[별표/별지]'→attachment 로 인식한다.

    - 정본 제목 헤더 맨 위 고정 (revision 인식)
    - kordoc가 반복한 문서 제목 헤딩 → 제거
    - '#{1,4} 제N장/편/절' → '## 제N장' (chapter)
    - '#{1,4} 제N조(...)' → '### 제N조(...)' (article; 파서는 #{2,3}만 인식)
    - '[별표/별지]' 포함 라인 → 원형 유지 (파서가 별도 처리)
    - 그 외 '# …' 등 최상위/부수 헤딩 → 굵은 텍스트로 강등
    """
    header = f"# {title} ({revision} 개정)" if revision else f"# {title}"
    title_key = re.sub(r"\s+", "", title)
    out: list[str] = [header]
    for ln in md.split("\n"):
        s = ln.rstrip()
        # 별표·별지 라인은 파서가 직접 처리 — 원형 유지
        if "[별표" in s or "[별지" in s:
            out.append(ln)
            continue
        m_art = _ARTICLE_RE.match(s)
        if m_art:
            out.append("### " + m_art.group(1).strip())
            continue
        m_ch = _CHAPTER_RE.match(s)
        if m_ch:
            out.append("## " + m_ch.group(1).strip())
            continue
        m_h = _HEADING_RE.match(s)
        if m_h:
            text = m_h.group(2).strip()
            if re.sub(r"\s+", "", text) == title_key:
                continue  # kordoc가 반복한 문서 제목 → 제거
            out.append(f"**{text}**" if text else "")
            continue
        out.append(ln)
    return "\n".join(out)


# ── 6) 추출본·매니페스트 기록 ───────────────────────────────────────────
def _clean_alio_managed(prev_sources: list[dict]) -> None:
    """직전 sync가 만든 추출본을 삭제 (ALIO에서 사라진 규정 정리)."""
    for s in prev_sources:
        f = EXTRACT_DIR / s.get("file", "")
        if f.name and f.exists():
            f.unlink()


def write_extracts_and_sources(resolved: list[dict]) -> list[dict]:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    prev = []
    if SOURCES_PATH.exists():
        try:
            prev = json.loads(SOURCES_PATH.read_text(encoding="utf-8")).get("sources", [])
        except Exception:  # noqa: BLE001
            prev = []
    _clean_alio_managed([s for s in prev if s.get("origin") == "alio"])

    sources: list[dict] = []
    used_names: dict[str, int] = {}
    written = 0
    for r in resolved:
        raw = MD_CACHE / f"{r['file_no']}.md"
        if not raw.exists():
            log(f"  추출본 없음(스킵): {r['title']}")
            continue
        md = raw.read_text(encoding="utf-8")
        norm = normalize_markdown(md, r["title"], r["revision"])
        base = f"{r['type']}_{safe_filename(r['title'])}"
        # 파일명 충돌 방지
        n = used_names.get(base, 0)
        used_names[base] = n + 1
        fname = f"{base}.md" if n == 0 else f"{base} ({n}).md"
        (EXTRACT_DIR / fname).write_text(norm, encoding="utf-8")
        written += 1
        sources.append({
            "name": r["title"],
            "type": r["type"],
            "file": fname,
            "revision": r["revision"],
            "file_no": r["file_no"],
            "origin": "alio",
        })

    SOURCES_PATH.write_text(
        json.dumps({
            "apbaId": APBA_ID, "reportFormRootNo": REPORT_ROOT,
            "list_url": LIST_REFERER,
            "count": len(sources),
            "sources": sources,
        }, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log(f"  추출본 {written}개 기록 + sources.json ({len(sources)}개 소스)")
    return sources


# ── 메인 ────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="ALIO KOICA 규정 동기화")
    ap.add_argument("--fresh", action="store_true", help="캐시 무시하고 처음부터")
    ap.add_argument("--no-build", action="store_true", help="인덱스 재빌드 생략")
    ap.add_argument("--limit", type=int, default=0, help="처음 N개만 (테스트용)")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    reg_cache = CACHE_DIR / "regulations.json"
    res_cache = CACHE_DIR / "resolved.json"

    # 1) 목록
    if reg_cache.exists() and not args.fresh:
        items = json.loads(reg_cache.read_text(encoding="utf-8"))
        log(f"[1/7] 목록: 캐시 {len(items)}개")
    else:
        log("[1/7] ALIO 규정 목록 조회…")
        items = fetch_regulation_list()
        reg_cache.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.limit:
        items = items[: args.limit]

    # 2) 현행본 해석
    if res_cache.exists() and not args.fresh:
        resolved = json.loads(res_cache.read_text(encoding="utf-8"))
        log(f"[2/7] 현행본: 캐시 {len(resolved)}개")
    else:
        log("[2/7] 현행본(최신 개정본) 해석…")
        resolved = resolve_current_files(items)
        res_cache.write_text(json.dumps(resolved, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.limit:
        resolved = resolved[: args.limit]

    # 3) 다운로드
    log("[3/7] 현행본 HWP 다운로드…")
    download_files(resolved)

    # 4) kordoc 변환
    log("[4/7] kordoc HWP→Markdown 변환…")
    kordoc_convert(resolved, fresh=args.fresh)

    # 5+6) 정규화 + 기록
    log("[5/7] 정규화(Format A) + [6/7] 추출본·sources.json 기록…")
    write_extracts_and_sources(resolved)

    # 7) 빌드
    if args.no_build:
        log("[7/7] 인덱스 재빌드 생략 (--no-build)")
    else:
        log("[7/7] 인덱스 재빌드…")
        import koica_search as ks
        arts, atts = ks.build_index()
        log(f"완료: {len(arts)}개 조문 + {len(atts)}개 별표·별지")


if __name__ == "__main__":
    main()
