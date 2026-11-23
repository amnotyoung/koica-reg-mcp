"""Propose updates to the map's version-pinned external-law excerpts via law.go.kr Open API."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import errno
from functools import lru_cache
from http.client import IncompleteRead
from itertools import islice
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from regulation_map.law_evidence import title_key

ORIGIN = "https://www.law.go.kr"
DRF_BASE = ORIGIN + "/DRF/"
PROVISION = re.compile(r"제(\d+)조(?:의(\d+))?(?:제(\d+)항)?(?:제(\d+(?:의\d+)?)호)?(?:([가-하])목)?")
ARTICLE_HEADER = re.compile(r"^제(\d+)조(?:의(\d+))?(?:\([^)]*\))?\s*")
HANG = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ANTIBOT_PROBE_BYTES = 4096
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data, *, sort=False):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort) + "\n", encoding="utf-8")
    tmp.replace(path)


def endpoint(page, **params):
    return ORIGIN + "/LSW/" + page + ".do?" + urlencode(params)


def api_url(page, **params):
    return DRF_BASE + page + "?" + urlencode(params)


def mask_sensitive(text):
    return re.sub(r"([?&]OC=)[^&\s\"']+", r"\1***", str(text), flags=re.IGNORECASE)


def get_api_key():
    key = os.environ.get("LAW_OC", "").strip()
    if not key:
        raise ValueError("LAW_OC 환경변수가 설정되지 않았습니다")
    return key


class IncompleteSourceResponse(ValueError):
    """The public API returned no usable document. A later fetch can recover."""


def temporary_source_error(exc):
    if isinstance(exc, HTTPError):
        return exc.code in (408, 425, 429, 500, 502, 503, 504)
    if isinstance(exc, URLError):
        return temporary_source_error(exc.reason)
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    return (isinstance(exc, (TimeoutError, ConnectionError, ssl.SSLEOFError, IncompleteRead,
                             IncompleteSourceResponse)) or
            isinstance(exc, OSError) and exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH,
                                                        errno.EADDRNOTAVAIL, errno.ETIMEDOUT))


@dataclass(frozen=True)
class SourceIssue:
    message: str
    retryable: bool


def result_code(errors):
    if not errors:
        return 0
    return 75 if all(e.retryable for e in errors) else 1


def parse_antibot_url(body):
    """Recover redirect path from law.go.kr cloud-IP JS anti-bot challenge."""
    a = re.search(r"t:'([^']*)',h:'([^']*)'", body)
    if a:
        o = re.search(r"o:'([^']*)'", body)
        if o:
            return a.group(1) + a.group(2) + o.group(1)
    b = re.search(r"o:'([^']*)',c:(\d+)\},z=(\d+)", body)
    if b:
        text, cut, skip = b.group(1), int(b.group(2)), int(b.group(3))
        return text[:cut] + text[cut + skip:]
    return None


def _request_headers():
    return {
        "User-Agent": os.environ.get("LAW_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": ORIGIN + "/",
        "Accept": "application/json, application/xml, text/xml, */*",
    }


def _open_once(url):
    if urlsplit(url).netloc != "www.law.go.kr":
        raise ValueError("unexpected source host")
    with urlopen(Request(url, headers=_request_headers()), timeout=20) as response:
        if urlsplit(response.geturl()).netloc != "www.law.go.kr":
            raise ValueError("unexpected response host")
        return response.read().decode("utf-8", errors="replace")


def _fetch_with_antibot(url, max_hops=3):
    text = _open_once(url)
    for _ in range(max_hops):
        prefix = text[:ANTIBOT_PROBE_BYTES]
        if "location.assign" not in prefix:
            return text
        path = parse_antibot_url(prefix)
        if not path:
            return text
        next_url = urljoin(url, path)
        if urlsplit(next_url).netloc != "www.law.go.kr":
            return text
        try:
            text = _open_once(next_url)
        except HTTPError as e:
            e.close()
            if e.code == 404:
                return _open_once(url)
            raise
    return text


@lru_cache(maxsize=16)
def fetch(url, *, title, stage):
    if urlsplit(url).netloc != "www.law.go.kr":
        raise ValueError("unexpected source host")
    for attempt in range(1, 4):
        started = time.monotonic()
        try:
            body = _fetch_with_antibot(url)
            if not body.strip():
                raise IncompleteSourceResponse("공식 API 응답 본문이 비어 있음")
            head = body.lstrip()[:200].lower()
            if head.startswith("<!doctype html") or head.startswith("<html"):
                raise IncompleteSourceResponse("공식 API가 HTML 안내 페이지를 반환함")
        except (OSError, IncompleteRead, ValueError) as e:
            elapsed = time.monotonic() - started
            if isinstance(e, HTTPError):
                e.close()
            retry = attempt < 3 and temporary_source_error(e)
            delay = 2 ** (attempt - 1)
            outcome = f"{delay}초 후 재시도" if retry else "요청 종료"
            print(f"요청 실패 · 법령={title} · 단계={stage} · 시도={attempt}/3 · 경과={elapsed:.2f}초"
                  f" · {type(e).__name__}: {mask_sensitive(e)} · {outcome}", flush=True)
            if not retry:
                raise
            time.sleep(delay)
        else:
            if attempt > 1:
                elapsed = time.monotonic() - started
                print(f"재시도 성공 · 법령={title} · 단계={stage} · 시도={attempt}/3 · 경과={elapsed:.2f}초", flush=True)
            return body


def fetch_xml(page, params, *, title, stage):
    url = api_url(page, OC=get_api_key(), type="XML", **params)
    body = fetch(url, title=title, stage=stage)
    try:
        return ET.fromstring(body)
    except ET.ParseError as e:
        raise IncompleteSourceResponse(f"공식 XML 파싱 실패: {e}") from e


def fetch_json(page, params, *, title, stage):
    url = api_url(page, OC=get_api_key(), type="JSON", **params)
    body = fetch(url, title=title, stage=stage)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise IncompleteSourceResponse(f"공식 JSON 파싱 실패: {e}") from e
    if not isinstance(data, dict) or not data:
        raise IncompleteSourceResponse("공식 JSON 응답이 비어 있음")
    return data


def xml_text(elem, tag):
    node = elem.find(tag)
    return (node.text or "").strip() if node is not None else ""


def today_kst():
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")


def normalize_ymd(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 8 else ""


def same_id(a, b):
    return str(a or "").strip().lstrip("0") == str(b or "").strip().lstrip("0")


def _to_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _flatten_content(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped and not stripped.startswith(("<img", "</img")):
                parts.append(item)
        elif isinstance(item, list):
            nested = _flatten_content(item)
            if nested:
                parts.append(nested)
    return "\n".join(parts)


def _group_mok_by_reset(moks):
    groups = []
    for mok in moks:
        if not isinstance(mok, dict):
            continue
        num = str(mok.get("목번호") or "").strip()
        if num.startswith("가") or not groups:
            groups.append([])
        groups[-1].append(mok)
    return groups


def _parse_admin_articles(blocks):
    """Split admin rule <조문내용> blocks into per-article line lists keyed by (jo, branch)."""
    articles = {}
    current_key = None
    for block in blocks:
        for raw_line in block.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            m = ARTICLE_HEADER.match(line)
            if m:
                current_key = (int(m.group(1)), int(m.group(2) or 0))
                articles.setdefault(current_key, [])
                rest = line[m.end():].strip()
                if rest:
                    articles[current_key].append(rest)
            elif current_key is not None:
                articles[current_key].append(line)
    return articles


@dataclass(frozen=True)
class Edition:
    admin: bool
    identity: str
    seq: str
    effective: str
    name: str

    def link(self, provision):
        if self.admin:
            return endpoint("admRulInfoP", admRulSeq=self.seq)
        jo, branch, *_ = PROVISION.fullmatch(provision).groups()
        return endpoint("lsInfoP", lsiSeq=self.seq, efYd=self.effective) + f"#J{jo}:{branch or '0'}"

    def document(self):
        if self.admin:
            root = fetch_xml("lawService.do", {"target": "admrul", "ID": self.seq},
                             title=self.name, stage="행정규칙 본문 조회")
            info = root.find("행정규칙기본정보")
            if info is None:
                raise IncompleteSourceResponse("행정규칙 기본정보 누락")
            rule_id = xml_text(info, "행정규칙ID")
            rule_seq = xml_text(info, "행정규칙일련번호")
            rule_name = xml_text(info, "행정규칙명")
            effective = normalize_ymd(xml_text(info, "시행일자") or xml_text(info, "발령일자"))
            blocks = [(n.text or "").strip() for n in root.findall(".//조문내용") if (n.text or "").strip()]
            if (not same_id(rule_id, self.identity) or rule_seq != self.seq
                    or effective != self.effective or title_key(rule_name) != title_key(self.name)
                    or not blocks):
                raise ValueError("본문 식별자·시행일 또는 본문 구조 불일치")
            return {"admin": True, "articles": _parse_admin_articles(blocks)}

        data = fetch_json("lawService.do",
                          {"target": "eflaw", "MST": self.seq, "efYd": self.effective},
                          title=self.name, stage="법령 본문 조회")
        law = data.get("법령")
        if not isinstance(law, dict):
            raise IncompleteSourceResponse("법령 본문 노드 누락")
        info = law.get("기본정보") or {}
        law_id = str(info.get("법령ID") or "").strip()
        effective = normalize_ymd(info.get("시행일자"))
        law_name = str(info.get("법령명_한글") or "").strip()
        units = _to_list((law.get("조문") or {}).get("조문단위"))
        if (not same_id(law_id, self.identity) or effective != self.effective
                or title_key(law_name) != title_key(self.name) or not units):
            raise ValueError("본문 식별자·시행일 또는 본문 구조 불일치")
        return law


def admin_edition(seq, title):
    root = fetch_xml("lawService.do", {"target": "admrul", "ID": seq},
                     title=title, stage="행정규칙 판본 정보 조회")
    info = root.find("행정규칙기본정보")
    if info is None:
        raise IncompleteSourceResponse("행정규칙 기본정보 누락")
    rule_seq = xml_text(info, "행정규칙일련번호")
    if rule_seq != seq:
        raise ValueError("행정규칙 일련번호 불일치")
    rule_id = xml_text(info, "행정규칙ID")
    effective = normalize_ymd(xml_text(info, "시행일자") or xml_text(info, "발령일자"))
    name = xml_text(info, "행정규칙명")
    if not rule_id.isdigit() or not effective or not name:
        raise IncompleteSourceResponse("행정규칙 판본 필드 누락")
    return Edition(True, rule_id, seq, effective, name)


def _search_current_admin(title, identity, today):
    queries = [title]
    stripped = re.sub(r"^\([^)]+\)\s*", "", title).strip()
    if stripped and stripped != title:
        queries.append(stripped)
    for query in queries:
        root = fetch_xml("lawSearch.do",
                         {"target": "admrul", "query": query, "nw": "1", "display": "100"},
                         title=title, stage="현행 행정규칙 검색")
        matches = []
        for row in root.findall(".//admrul"):
            if not same_id(xml_text(row, "행정규칙ID"), identity):
                continue
            status = xml_text(row, "현행연혁구분") or xml_text(row, "현행여부")
            if status and status != "현행":
                continue
            seq = xml_text(row, "행정규칙일련번호")
            effective = normalize_ymd(xml_text(row, "시행일자") or xml_text(row, "발령일자"))
            name = xml_text(row, "행정규칙명")
            if seq.isdigit() and effective and effective <= today and name:
                matches.append((effective, int(seq), seq, name))
        if matches:
            matches.sort(reverse=True)
            effective, _, seq, name = matches[0]
            return Edition(True, identity, seq, effective, name)
    return None


def current_edition(title, record):
    today = today_kst()
    if "adminSeq" in record:
        identity = record.get("adminId")
        bootstrapped = None
        if not identity:
            bootstrapped = admin_edition(record["adminSeq"], title)
            identity = bootstrapped.identity
        edition = _search_current_admin(title, identity, today)
        if edition is None:
            edition = bootstrapped or admin_edition(record["adminSeq"], title)
        if not edition.seq.isdigit() or not edition.identity.isdigit():
            raise ValueError("잘못된 판본 식별자")
        if edition.effective > today:
            raise ValueError("시행예정 판본은 갱신하지 않음")
        return edition

    identity = record["lawId"]
    root = fetch_xml("lawSearch.do",
                     {"target": "eflaw", "LID": identity, "nw": "3", "display": "100"},
                     title=title, stage="현행 법령 판본 조회")
    matches = []
    for row in root.findall(".//law"):
        if not same_id(xml_text(row, "법령ID"), identity):
            continue
        status = xml_text(row, "현행연혁코드")
        if status and status != "현행":
            continue
        seq = xml_text(row, "법령일련번호")
        effective = normalize_ymd(xml_text(row, "시행일자"))
        name = xml_text(row, "법령명한글")
        if seq.isdigit() and effective and effective <= today and name:
            matches.append((effective, int(seq), seq, name))
    if not matches:
        raise IncompleteSourceResponse("공식 API에서 현행 판본을 찾을 수 없음")
    matches.sort(reverse=True)
    effective, _, seq, name = matches[0]
    edition = Edition(False, identity, seq, effective, name)
    if not edition.seq.isdigit() or not edition.identity.isdigit():
        raise ValueError("잘못된 판본 식별자")
    if edition.effective > today:
        raise ValueError("시행예정 판본은 갱신하지 않음")
    return edition


def _statute_article_lines(law, jo, branch):
    want_num = int(jo)
    want_branch = int(branch or 0)
    units = _to_list((law.get("조문") or {}).get("조문단위"))
    unit = None
    for candidate in units:
        if not isinstance(candidate, dict) or candidate.get("조문여부") != "조문":
            continue
        num = str(candidate.get("조문번호") or "").strip()
        br = str(candidate.get("조문가지번호") or "0").strip() or "0"
        if num.isdigit() and br.isdigit() and int(num) == want_num and int(br) == want_branch:
            unit = candidate
            break
    if unit is None:
        return None

    lines = []
    base = _flatten_content(unit.get("조문내용"))
    for raw in base.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if not lines:
            line = ARTICLE_HEADER.sub("", line, count=1).strip()
        if line:
            lines.append(line)

    for hang in _to_list(unit.get("항")):
        if not isinstance(hang, dict):
            continue
        hang_text = _flatten_content(hang.get("항내용"))
        for raw in hang_text.splitlines():
            line = " ".join(raw.split())
            if line:
                lines.append(line)

        hos = [h for h in _to_list(hang.get("호")) if isinstance(h, dict)]
        hang_moks = [m for m in _to_list(hang.get("목")) if isinstance(m, dict)]
        mok_groups = _group_mok_by_reset(hang_moks)
        alignable = bool(hos) and len(mok_groups) == len(hos)

        for idx, ho in enumerate(hos):
            for raw in _flatten_content(ho.get("호내용")).splitlines():
                line = " ".join(raw.split())
                if line:
                    lines.append(line)
            for mok in _to_list(ho.get("목")):
                if isinstance(mok, dict):
                    for raw in _flatten_content(mok.get("목내용")).splitlines():
                        line = " ".join(raw.split())
                        if line:
                            lines.append(line)
            if alignable:
                for mok in mok_groups[idx]:
                    for raw in _flatten_content(mok.get("목내용")).splitlines():
                        line = " ".join(raw.split())
                        if line:
                            lines.append(line)

        if not alignable and hang_moks:
            for mok in hang_moks:
                for raw in _flatten_content(mok.get("목내용")).splitlines():
                    line = " ".join(raw.split())
                    if line:
                        lines.append(line)
    return lines


def _narrow_lines(lines, hang, ho, mok):
    if not lines:
        return None
    if hang and not 1 <= int(hang) <= len(HANG):
        return None
    levels = [
        (HANG[int(hang) - 1] if hang else None, f"[{HANG}]"),
        (ho + "." if ho else None, r"\d+(?:의\d+)?\."),
        (mok + "." if mok else None, r"[가-하]\."),
    ]
    boundaries = []
    for marker, pattern in levels:
        boundaries.append(pattern)
        if marker is None:
            continue
        matches = [i for i, line in enumerate(lines)
                   if (match := re.match(pattern, line)) and match[0] == marker]
        if len(matches) != 1:
            return None
        start, = matches
        end = next((i for i in range(start + 1, len(lines))
                    if re.match("|".join(boundaries), lines[i])), len(lines))
        lines = lines[start:end]
    return "\n".join(lines) or None


def source_text(doc, provision):
    """Select exactly one article, then narrow within its parent 항/호/목."""
    m = PROVISION.fullmatch(provision)
    if not m:
        raise ValueError(f"unsupported provision: {provision}")
    jo, branch, hang, ho, mok = m.groups()
    if isinstance(doc, dict) and doc.get("admin"):
        lines = list(doc["articles"].get((int(jo), int(branch or 0)), ()))
    else:
        lines = _statute_article_lines(doc, jo, branch)
    if not lines:
        return None
    return _narrow_lines(lines, hang, ho, mok)


def pinned(excerpt):
    url = urlsplit(excerpt["url"])
    return (excerpt["version"].startswith(("연혁", "대통령훈령 제")) or url.netloc != "www.law.go.kr"
            or url.path not in ("/LSW/lsInfoP.do", "/LSW/admRulInfoP.do"))


def compact(text):
    quotes = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
    return re.sub(r"\s+", "", text).translate(quotes).rstrip("…")


def update_excerpt(old, title, provision, edition, document):
    params = parse_qs(urlsplit(old["url"]).query)
    seq = params["admRulSeq" if edition.admin else "lsiSeq"][0]
    date_label = datetime.strptime(edition.effective, "%Y%m%d").strftime("%Y.%m.%d 시행")
    old_date = re.fullmatch(r"(\d{4}\.\d{2}\.\d{2}) 시행", old["version"])
    if old_date and edition.effective < old_date[1].replace(".", ""):
        raise ValueError("공식 조회 시행일이 저장된 판본보다 과거임")
    if seq == edition.seq and (edition.admin or date_label == old["version"]) and title_key(title) == title_key(edition.name):
        result = dict(old)
        result.pop("sync_review", None)
        return result, "기존 판본 재확인"
    url = edition.link(provision)
    if title_key(title) != title_key(edition.name):
        document()  # Verify identity before proposing a title-change review.
        reason = "법령 제명 변경"
    else:
        body = source_text(document(), provision)
        if not body or re.match(r"^(?:[①-⑳]|\d+\.)?\s*삭제(?:\s|$)", body):
            reason = "조문 삭제·이동 또는 번호 확인 필요"
        else:
            text = body if len(body) <= 245 else body[:245].rsplit(" ", 1)[0] + "…"
            # Preserve the existing editorial cut if it remains an exact prefix.
            if old["text"].endswith("…") and compact(body).startswith(compact(old["text"])):
                text = old["text"]
            unchanged = compact(text) == compact(old["text"])
            result = {**old, "text": old["text"] if unchanged else text, "version": date_label, "url": url}
            result.pop("sync_review", None)
            return result, "판본 변경 · 발췌 동일" if unchanged else "발췌 변경 · 내규 연결 재검토"
    return {**old, "sync_review": {"reason": reason, "title": edition.name, "version": date_label, "url": url}}, reason


def sync(mapping, excerpts, resolve=current_edition):
    # A single current statute may also have an intentionally historical excerpt.
    names = {title_key(alias): name for name, record in mapping["laws"].items()
             for alias in [name, *record.get("aliases", [])]}
    grouped = defaultdict(list)
    changes, errors, skipped = [], [], Counter()
    for title, provisions in excerpts.items():
        for coord, old in provisions.items():
            if pinned(old):
                skipped["연혁·고정 공개본"] += 1
            elif title not in names:
                errors.append(SourceIssue(f"{title} {coord}: 법령 식별자 확인 필요", False))
            else:
                grouped[names[title]].append((title, coord, old))

    def compare_one(title):
        record = mapping["laws"][title]
        edition = resolve(title, record)
        document = lru_cache(maxsize=1)(edition.document)
        pending = []
        for key, coord, old in grouped[title]:
            result, reason = update_excerpt(old, title, coord, edition, document)
            if result != old:
                pending.append((key, coord, result, reason))
        return edition, pending

    # Bound both the load on the publication site and the work sent after an outage.
    titles = iter(grouped)
    completed = consecutive_failures = 0
    with ThreadPoolExecutor(max_workers=3) as workers:
        while batch := list(islice(titles, 3)):
            futures = [(title, workers.submit(compare_one, title)) for title in batch]
            for title, future in futures:
                completed += 1
                try:
                    edition, pending = future.result()
                except (ValueError, KeyError, OSError, IndexError, IncompleteRead) as e:
                    issue = SourceIssue(f"{title}: {type(e).__name__}: {mask_sensitive(e)}", temporary_source_error(e))
                    errors.append(issue)
                    print(f"공식 조회 실패 · {issue.message} · 재시도 대상={issue.retryable}", flush=True)
                    consecutive_failures += 1
                    continue
                consecutive_failures = 0
                for key, coord, result, reason in pending:
                    excerpts[key][coord] = result
                    changes.append((title, coord, reason))
                record = mapping["laws"][title]
                if edition.admin and title_key(title) == title_key(edition.name):
                    if record.get("adminId") != edition.identity or record["adminSeq"] != edition.seq:
                        record.update(adminId=edition.identity, adminSeq=edition.seq)
                        changes.append((title, "", "행정규칙 식별자 갱신"))
            print(f"공식 판본 대조 {completed}/{len(grouped)} · 오류 {len(errors)}", flush=True)
            if consecutive_failures >= 4 and completed < len(grouped):
                errors.append(SourceIssue(f"연속 조회 실패로 나머지 {len(grouped)-completed}개 문서 대조 중단",
                                          all(e.retryable for e in errors)))
                break
    return changes, errors, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / "regulation_map")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    mp, ep = args.dir / "law_mapping.json", args.dir / "law_excerpts.json"
    mapping, excerpts = read_json(mp), read_json(ep)
    changes, errors, skipped = sync(mapping, excerpts)
    report = ["## 상위법령 주간 대조", "", f"변경 {len(changes)}건 · 조회 오류 {len(errors)}건 · 연혁·고정 발췌 {sum(skipped.values())}건"]
    if changes:
        report.extend(["", "발췌와 시행일·공식 링크의 전후 차이는 Files changed에서 확인하세요. 참고 조문은 변경 후에도 해당 내규의 문맥에 맞는지 검토하세요.", ""])
        for title, coord, reason in changes[:150]:
            affected = [name for name, row in mapping["mappings"].items()
                        if coord and any(s.get("provision") == coord for s in row.get("related_provisions", {}).get(title, []))]
            context = " / 참고 연결 재검토: " + ", ".join(affected) if affected else ""
            report.append(f"- {title} {coord} — {reason}{context}")
        if len(changes) > 150:
            report.append(f"- 나머지 {len(changes)-150}건은 파일 차이에서 확인하세요.")
    if errors:
        report.extend(["", "### 조회 실패 · 이전 판본 유지", "", *("- " + err.message for err in errors)])
    args.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(report[2], flush=True)
    if changes:
        write_json(mp, mapping)
        write_json(ep, excerpts, sort=True)
    return result_code(errors)


if __name__ == "__main__":
    sys.exit(main())
