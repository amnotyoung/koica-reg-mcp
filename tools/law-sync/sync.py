"""Propose updates to the map's version-pinned external-law excerpts from law.go.kr."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import URLError

from lxml import html

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from regulation_map.law_evidence import title_key

ORIGIN = "https://www.law.go.kr"
PROVISION = re.compile(r"제(\d+)조(?:의(\d+))?(?:제(\d+)항)?(?:제(\d+(?:의\d+)?)호)?(?:([가-하])목)?")
HANG = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data, *, sort=False):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort) + "\n", encoding="utf-8")
    tmp.replace(path)


def endpoint(page, **params):
    return ORIGIN + "/LSW/" + page + ".do?" + urlencode(params)


@lru_cache(maxsize=8)
def fetch(url):
    if urlsplit(url).netloc != "www.law.go.kr":
        raise ValueError("unexpected source host")
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "koica-reg-mcp weekly law review"}), timeout=20) as response:
                if urlsplit(response.geturl()).netloc != "www.law.go.kr":
                    raise ValueError("unexpected response host")
                return html.fromstring(response.read().decode("utf-8"))
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def field(doc, key):
    values = set(doc.xpath('//input[@id=$key]/@value', key=key))
    if len(values) != 1 or not all(values):
        raise ValueError(f"missing or ambiguous portal field: {key}")
    return values.pop()


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
            doc = fetch(endpoint("admRulLsInfoR", admRulSeq=self.seq, chrClsCd="010202"))
        else:
            doc = fetch(endpoint("lsInfoR", lsiSeq=self.seq, lsId=self.identity, efYd=self.effective,
                                 chrClsCd="010202", lsNm=self.name))
        if (field(doc, "admRulId" if self.admin else "lsId") != self.identity
                or field(doc, "lsiSeq") != self.seq or field(doc, "efYd") != self.effective
                or field(doc, "lsNm") != self.name or not doc.xpath('//div[@class="lawcon"]//input[@name="joNoList"]')):
            raise ValueError("본문 식별자·시행일 또는 본문 구조 불일치")
        return doc


def admin_edition(seq):
    doc = fetch(endpoint("admRulInfoP", admRulSeq=seq))
    if field(doc, "admRulSeq") != seq:
        raise ValueError("행정규칙 일련번호 불일치")
    return Edition(True, field(doc, "admRulId"), seq, field(doc, "efYd"), field(doc, "admNm"))


def current_edition(title, record):
    admin = "adminSeq" in record
    if admin:
        name = title
        identity = record.get("adminId") or admin_edition(record["adminSeq"]).identity
    else:
        identity = record["lawId"]
        doc = fetch(endpoint("lsInfoP", lsId=identity))
        if field(doc, "lsId") != identity:
            raise ValueError("법령 ID 불일치")
        name = field(doc, "lsNm")
    # The ID landing page uses promulgation dates for some phased amendments.
    # The official Korean permalink resolves the actually effective slice.
    category = "행정규칙" if admin else "법령"
    wrapper = fetch(ORIGIN + "/" + quote(category) + "/" + quote(name, safe=""))
    frames = wrapper.xpath('//iframe[@id="lawService"]/@src')
    if len(frames) != 1:
        raise ValueError("현행 판본 주소 확인 필요")
    params = parse_qs(urlsplit(frames[0]).query)
    seq = params["admRulSeq" if admin else "lsiSeq"][0]
    if not seq.isdigit():
        raise ValueError("잘못된 일련번호")
    if admin:
        edition = admin_edition(seq)
        if edition.identity != identity:
            raise ValueError("동명 행정규칙 또는 식별자 변경")
    else:
        edition = Edition(False, identity, seq, params["efYd"][0], name)
    if not edition.seq.isdigit() or not edition.identity.isdigit():
        raise ValueError("잘못된 판본 식별자")
    today = datetime.now(timezone(timedelta(hours=9))).date()
    if datetime.strptime(edition.effective, "%Y%m%d").date() > today:
        raise ValueError("시행예정 판본은 갱신하지 않음")
    return edition


def source_text(doc, provision):
    """Select exactly one article, then narrow within its parent 항/호/목."""
    m = PROVISION.fullmatch(provision)
    if not m:
        raise ValueError(f"unsupported provision: {provision}")
    jo, branch, hang, ho, mok = m.groups()
    code = f"Y{int(jo):04}{int(branch or 0):02}"
    boxes = doc.xpath('//input[@name="joNoList" and (@id=$a or @id=$b)]/ancestor::div[@class="lawcon"][1]',
                      a=code, b=code+"0000")
    if len(boxes) != 1:
        return None
    box = deepcopy(boxes[0])
    for element in box.xpath('.//label | .//span[@class="sfon" or @class="sfon2"]'):
        element.drop_tree()
    lines = [" ".join(p.text_content().split()) for p in box.xpath('./p')]
    lines = [line for line in lines if line]
    if hang and not 1 <= int(hang) <= len(HANG):
        return None
    levels = [
        (HANG[int(hang)-1] if hang else None, f"[{HANG}]"),
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
        end = next((i for i in range(start+1, len(lines))
                    if re.match("|".join(boundaries), lines[i])), len(lines))
        lines = lines[start:end]
    return "\n".join(lines) or None


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
                errors.append(f"{title} {coord}: 법령 식별자 확인 필요")
            else:
                grouped[names[title]].append((title, coord, old))
    consecutive_failures = 0
    for number, (title, items) in enumerate(grouped.items(), 1):
        if number % 25 == 0:
            print(f"공식 판본 확인 중 {number}/{len(grouped)}", flush=True)
        record = mapping["laws"][title]
        try:
            edition = resolve(title, record)
            document = lru_cache(maxsize=1)(edition.document)
            pending = []
            for key, coord, old in items:
                result, reason = update_excerpt(old, title, coord, edition, document)
                if result != old:
                    pending.append((key, coord, result, reason))
            # Only commit a document's proposals after every fetch for it succeeded.
            for key, coord, result, reason in pending:
                excerpts[key][coord] = result
                changes.append((title, coord, reason))
            if edition.admin and title_key(title) == title_key(edition.name):
                if record.get("adminId") != edition.identity or record["adminSeq"] != edition.seq:
                    record.update(adminId=edition.identity, adminSeq=edition.seq)
                    changes.append((title, "", "행정규칙 식별자 갱신"))
            consecutive_failures = 0
        except (ValueError, KeyError, OSError, IndexError) as e:
            errors.append(f"{title}: {e}")
            consecutive_failures += 1
            if consecutive_failures >= 4 and number < len(grouped):
                errors.append(f"연속 조회 실패로 나머지 {len(grouped)-number}개 문서 대조 중단")
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
        report.extend(["", "### 조회 실패 · 이전 판본 유지", "", *("- " + err for err in errors)])
    args.report.write_text("\n".join(report)+"\n", encoding="utf-8")
    print(report[2], flush=True)
    if changes:
        write_json(mp, mapping)
        write_json(ep, excerpts, sort=True)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
