"""Build an offline regulation map from the checked-in public regulation corpus.

Run from any directory: python3 regulation_map/build.py
No model, API, index rebuild, or third-party package is required.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import koica_search as ks
from regulation_map.reviews import apply_reviews, load_delegation
from regulation_map.documents import document_sections

ORG = "직제규정 시행세칙"
OUT = Path(__file__).parent


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


class TableParser(HTMLParser):
    """Expand merged cells while retaining shared-duty provenance."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            attrs = dict(attrs)
            self.cell = {"text": "", "rows": int(attrs.get("rowspan", 1)),
                         "cols": int(attrs.get("colspan", 1)), "header": tag == "th"}
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.row)

    def grid(self):
        pending = {}
        for row in self.rows:
            cells = {col: cell for col, (cell, count) in pending.items()}
            pending = {col: (cell, count - 1) for col, (cell, count) in pending.items() if count > 1}
            col = 0
            for cell in row:
                while col in cells:
                    col += 1
                for c in range(col, col + cell["cols"]):
                    cells[c] = cell
                    if cell["rows"] > 1:
                        pending[c] = (cell, cell["rows"] - 1)
                col += cell["cols"]
            yield [cells.get(c) for c in range(max(cells, default=-1) + 1)]


def split_duties(text, shared=False):
    starts = list(re.finditer(r"(?m)^\s*(\d+)\.\s*", text))
    if not starts:
        raise ValueError("번호가 있는 업무분장을 찾지 못했습니다")
    duties = []
    for i, match in enumerate(starts):
        body = text[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(text)].strip()
        duties.append({"number": match.group(1), "title": body.splitlines()[0],
                       "text": body, "shared": shared})
    return duties


def parse_departments(text):
    """Read annex 4, including HTML rowspan/colspan and the Markdown audit row."""
    match = re.search(r"^## \[별표\s*4\](.*?)(?=^## \[별표\s*5\])", text, re.M | re.S)
    if not match:
        raise ValueError("직제규정 시행세칙 별표 4/5 경계를 찾지 못했습니다")
    departments = []
    for section in re.split(r"❑\s*", match.group(1))[1:]:
        division = section.splitlines()[0].strip()
        tables = re.findall(r"<table>.*?</table>", section, re.S)
        # The independent audit department is a Markdown table.
        markdown = []
        for line in section.splitlines():
            if line.startswith("|") and re.search(r"\|\s*1\.", line):
                columns = line.strip().strip("|").split("|")
                markdown.append("<tr>" + "".join(f"<td>{c}</td>" for c in columns) + "</tr>")
        if markdown:
            tables.append('<table><tr><th>부서명</th><th>분장업무</th></tr>' + "".join(markdown) + "</table>")
        if not tables:
            raise ValueError(f"업무분장 표가 없습니다: {division}")
        for table in tables:
            parser = TableParser()
            parser.feed(table)
            boundary = None
            prior_names = []
            for row in parser.grid():
                if any(c and c["header"] for c in row):
                    boundary = next(i for i, c in enumerate(row) if c and "분장업무" in c["text"])
                    continue
                if boundary is None or len(row) <= boundary or any(c is None for c in row):
                    raise ValueError(f"지원하지 않는 업무분장 표 구조: {division}")
                names = []
                for i, cell in enumerate(row[:boundary]):
                    name = compact(cell["text"])
                    if not name and i < len(prior_names):
                        name = prior_names[i]
                    names.append(name)
                prior_names = names
                path = list(dict.fromkeys(n for n in names if n))
                duties, seen = [], set()
                for cell in row[boundary:]:
                    if id(cell) in seen:
                        continue
                    seen.add(id(cell))
                    duties.extend(split_duties(cell["text"], shared=cell["rows"] > 1))
                if not path or not duties:
                    raise ValueError(f"빈 부서/업무: {division}")
                key = " / ".join(path)
                for i, duty in enumerate(duties):
                    duty["id"] = f"d{i + 1}"
                departments.append({"id": key, "name": path[-1], "path": path,
                                    "division": division, "duties": duties})
    if len({d["id"] for d in departments}) != len(departments):
        raise ValueError("중복된 부서 ID")
    return departments


OVERSEAS = "해외사무소"


def split_overseas_duties(text):
    """Annex 5 numbers sub-steps as 5-1 and wraps one item over several lines."""
    starts = list(re.finditer(r"(?m)^\s*(\d+(?:-\d+)?)\.\s*", text))
    if not starts:
        raise ValueError("번호가 있는 해외사무소 업무를 찾지 못했습니다")
    duties = []
    for i, match in enumerate(starts):
        body = text[match.end():starts[i + 1].start() if i + 1 < len(starts) else len(text)].strip()
        lines = body.splitlines()
        title = lines[0]
        # A wrapped title continues on lines that are not "-" detail lines.
        for line in lines[1:]:
            if line.lstrip().startswith("-"):
                break
            title += " " + line.strip()
        duties.append({"number": match.group(1), "title": re.sub(r"\s+", " ", title).strip(" ,"),
                       "text": body, "shared": False})
    return duties


def parse_overseas_offices(text):
    """Read the overseas-office column of annex 5 and the office names of annex 2-2.

    Every overseas office has the same duties, so the column becomes one office
    group with one child per annex 5 category. The head-office column of the same
    row is kept only as context; it is already covered by annex 4.
    """
    match = re.search(r"^## \[별표\s*5\](.*?)(?=^## \[별표\s*6\])", text, re.M | re.S)
    if not match:
        raise ValueError("직제규정 시행세칙 별표 5/6 경계를 찾지 못했습니다")
    offices = re.search(r"^## \[별표\s*2의\s*2\](.*?)(?=^## \[별표)", text, re.M | re.S)
    names = re.findall(r"\|\s*\d+\s*\|\s*한국국제협력단\s*(\S+?사무소)\s*\|", offices.group(1)) if offices else []
    if not names:
        raise ValueError("직제규정 시행세칙 별표 2의 2 해외사무소 목록을 찾지 못했습니다")
    tables = re.findall(r"<table>.*?</table>", match.group(1), re.S)
    if len(tables) != 1:
        raise ValueError("별표 5 업무분장 표는 하나여야 합니다")
    parser = TableParser()
    parser.feed(tables[0])
    rows = list(parser.grid())
    header = [compact(c["text"]) for c in rows[0]]
    if header != ["구분", OVERSEAS, "본사"]:
        raise ValueError(f"지원하지 않는 별표 5 구조: {header}")
    groups = {}
    for row in rows[1:]:
        if len(row) != 3 or any(c is None for c in row):
            raise ValueError("지원하지 않는 별표 5 행 구조")
        category = compact(row[0]["text"])
        if row[1] is row[2]:
            # A note spanning both columns applies to the whole category.
            groups[category]["notes"].append(re.sub(r"\s*\n\s*", " ", row[1]["text"]).strip())
            continue
        if category in groups:
            raise ValueError(f"중복된 별표 5 구분: {category}")
        duties = split_overseas_duties(row[1]["text"])
        for i, duty in enumerate(duties):
            duty["id"] = f"d{i + 1}"
        groups[category] = {"id": f"{OVERSEAS} / {category}", "name": category, "path": [OVERSEAS, category],
                            "division": OVERSEAS, "duties": duties, "notes": [],
                            "citation": f"{ORG} [별표 5]",
                            "counterpart": {"label": "같은 구분의 본사 분장업무", "text": row[2]["text"].strip()},
                            "office_names": names}
    return list(groups.values())


ALIAS_DEFINITION = re.compile(r'[\s」』》〉"”]*\(이하\s*[“"\'‘]([^”"\'’]{2,15})[”"\'’]\s*(?:이)?라\s*한다\.?\)')
# Words that make "규정 제N조" point at the citing text itself or at an unnamed rule.
NOT_ALIAS_USE = re.compile(r"(?:^|[^가-힣])(?:이|본|동|같은|다른|각|해당|관련|타)\s*$")
PAIRED_TITLE = re.compile(r"([가-힣]{1,6})\s*[·ㆍ]\s*([가-힣]{1,6})(\s*[가-힣 ]{2,30}?(?:규정|지침|세칙|규칙))")


def build_relations(articles, sources):
    """Only exact, whitespace-tolerant full regulation names create edges.

    An article target is retained only if it exists in the main provisions.
    Document mentions are not promoted to delegation or legal hierarchy.
    """
    names = sorted(sources, key=len, reverse=True)
    pattern = re.compile("|".join(r"\s*".join(map(re.escape, name)) for name in names))
    lookup = {compact(name): name for name in names}
    article_keys = {(a.source, a.article) for a in articles}
    edges = {}

    def add(a, target, target_article, m):
        edge = edges.setdefault((a.source, target), {"from": a.source, "to": target, "kind": "explicit", "evidence": []})
        evidence = {"article": a.article, "title": a.article_title,
                    "target_article": target_article,
                    "quote": a.body[max(0, m.start() - 65):m.end() + 110].strip()}
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)

    # An abbreviation defined after a full title, e.g. 「감사규정」(이하 "규정"이라 한다), is
    # followed within the same regulation only. An abbreviation given to two titles is ambiguous.
    aliases = {}
    for a in articles:
        for m in pattern.finditer(a.body):
            d = ALIAS_DEFINITION.match(a.body, m.end())
            if d and lookup[compact(m.group())] != a.source:
                aliases.setdefault(a.source, {}).setdefault(d[1], set()).add(lookup[compact(m.group())])
    aliases = {source: {alias: targets.pop() for alias, targets in found.items() if len(targets) == 1}
               for source, found in aliases.items()}

    for a in articles:
        titled = []
        for m in pattern.finditer(a.body):
            titled.append(m.span())
            # Reject title suffix matches inside a longer Korean title.
            if m.start() and re.match(r"[가-힣A-Za-z]", a.body[m.start() - 1]):
                continue
            target = lookup[compact(m.group())]
            if target == a.source:
                continue
            tail = a.body[m.end():]
            # A longer, unknown implementation-rule title is not its parent.
            if re.match(r"\s*(?:시행세칙|시행규칙|시행지침)", tail):
                continue
            # Keep the article number across an abbreviation such as (이하 "규정"이라 한다).
            ref = re.match(r'[\s」』》〉"”]*(?:\(이하[^)]*\)\s*)?제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', tail)
            target_article = ""
            if ref:
                target_article = f"제{ref[1]}조" + (f"의{ref[2]}" if ref[2] else "")
                if (target, target_article) not in article_keys:
                    continue
            add(a, target, target_article, m)
        for alias, target in aliases.get(a.source, {}).items():
            for m in re.finditer(r"(?<![가-힣A-Za-z])" + re.escape(alias) + r'[」』"”]?\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', a.body):
                if NOT_ALIAS_USE.search(a.body[max(0, m.start() - 6):m.start()]):
                    continue
                if any(start < m.end() and m.start() < end for start, end in titled):
                    continue
                target_article = f"제{m[1]}조" + (f"의{m[2]}" if m[2] else "")
                if (target, target_article) in article_keys:
                    add(a, target, target_article, m)
        # Paired titles such as "국내·국외 여비규정": the last title is matched above, so add
        # the first only when it is a real title. Any article number is ambiguous, so none is kept.
        for m in PAIRED_TITLE.finditer(a.body):
            first = lookup.get(compact(m.group(1) + m.group(3)))
            last = lookup.get(compact(m.group(2) + m.group(3)))
            if not first or not last or first == a.source:
                continue
            if re.match(r"\s*(?:시행세칙|시행규칙|시행지침)", a.body[m.end():]):
                continue
            add(a, first, "", m)
    return list(edges.values())


ALIO_FILE_URL = "https://www.alio.go.kr/download/rulefiledown.json?fileNo={}"


def official_source(entry, list_url):
    """Point every extracted text back to its ALIO publication and file.

    Only values recorded by the ALIO sync manifest are used; a missing value is
    left empty rather than guessed.
    """
    entry = entry or {}
    file_no = str(entry.get("file_no") or "")
    return {
        "publisher": "ALIO 공공기관 경영정보 공개시스템",
        "page_url": entry.get("page_url") or "",
        "list_url": list_url or "",
        "file_url": ALIO_FILE_URL.format(file_no) if file_no.isdigit() else "",
        "file_no": file_no,
        "revision": entry.get("revision") or "",
        "collected_at": entry.get("collected_at") or "",
    }


def build():
    articles = []
    paths = []
    for path in sorted(ks.DATA_DIR.glob("*.md")):
        if path.name in ks.CATEGORY_AGGREGATE_FILES or "_" not in path.stem:
            continue
        parsed, _ = ks.parse_md(path, path.stem.split("_", 1)[0])
        articles.extend(a for a in parsed if not a.is_supplementary and "삭제" not in a.article_title)
        paths.append(path)
    # Some source documents contain duplicated article headings; retain unique bodies.
    articles = list({(a.source, a.article, a.body): a for a in articles}.values())
    org_path = ks.DATA_DIR / f"시행세칙_{ORG}.md"
    org_text = org_path.read_text(encoding="utf-8")
    departments = parse_departments(org_text) + parse_overseas_offices(org_text)
    sources = {}
    for a in articles:
        source = sources.setdefault(a.source, {"id": a.source, "category": a.category, "revision": a.revision, "articles": []})
        source["articles"].append({"article": a.article, "title": a.article_title, "body": a.body})
    relations = build_relations(articles, sources)
    catalog = json.loads((OUT / "content_reviews.json").read_text(encoding="utf-8"))
    source_texts = {ks.parse_md(p, p.stem.split("_", 1)[0])[0][0].source: p.read_text(encoding="utf-8") for p in paths}
    review_stats = apply_reviews(departments, articles, source_texts, catalog, relations,
                                 load_delegation(catalog, OUT))
    manifest = json.loads((ks.DATA_DIR.parent / "sources.json").read_text(encoding="utf-8"))
    official = {s["name"]: s for s in manifest["sources"]}
    for name, source in sources.items():
        source["document"] = {"sections": document_sections(source_texts[name]),
                              "format": "repository_markdown_extraction"}
        source["official"] = official_source(official.get(name), manifest.get("list_url"))
    supplement = org_text.split("부칙<", 1)[-1].split("## [별표", 1)[0]
    notices = [line.strip() for line in supplement.splitlines() if "다만" in line]
    fingerprint = hashlib.sha256()
    for path in paths:
        fingerprint.update(path.name.encode())
        fingerprint.update(path.read_bytes())
    data = {"meta": {"organization_source": ORG, "organization_revision": sources[ORG]["revision"],
                     "citation": f"{ORG} [별표 4]", "notices": notices, "reviews": review_stats,
                     "corpus_sha256": fingerprint.hexdigest(),
                     "scope": "저장소 공개 규정 스냅샷 · 본칙의 명시적 규정명 참조 · 별표 4 부서 업무분장 · 별표 5 해외사무소 업무분장",
                     "limits": "참조 추출은 완전하지 않습니다. 다른 규정에서 정의한 약칭·간접인용·별표 안의 참조는 포함하지 않습니다. 업무 연결은 근거 조문을 읽고 작성한 AI 내용검토이며 기관의 공식 적용 판정이 아닙니다. 미검토 업무를 자동으로 연결하지 않습니다."},
            "departments": departments, "regulations": list(sources.values()), "relations": relations}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    (OUT / "data.js").write_text("window.REGULATION_MAP = " + payload + ";\n", encoding="utf-8")
    print(f"규정 지도 생성: {len(departments)}개 부서 / {sum(len(d['duties']) for d in departments)}개 업무 / {len(sources)}종 규정 / {len(relations)}개 참조 관계")
    return data


if __name__ == "__main__":
    build()
