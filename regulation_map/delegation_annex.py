"""Extract 위임전결규정 [별표 1-2] from the last table ALIO published.

The current regulation text says only "별도 파일 참조" for this annex, and ALIO
has not posted that file since the 2024.02.13 revision. The map therefore shows
the 2024 table as an outdated reference, never as the current rule.

    python3 regulation_map/delegation_annex.py --download
    python3 regulation_map/delegation_annex.py --zip path/to/194590.zip
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "delegation_annex_2024.json"
FILE_NO = "194590"
FILE_NAME = "위임전결규정(2024년도 02월 개정).zip"
ZIP_SHA256 = "0a19171d02324c4ec6bae76a8baf44015e9a95c3d45633ddd603e2927dbf47c4"
PAGE_URL = ("https://www.alio.go.kr/item/itemBoard21110.do?disclosureNo=&apbaId=C0146&nowcode=21110"
            "&reportFormNo=21110&table_name=COMM_RULE&idx_name=RULE_NO&idx=6114&reportGbn=Y&bid_type=K1400")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
HEADER = ["번호", "업무종류", "단위업무", "세부업무", "이사장", "본부장", "처장",
          "실, 원, 센터, 국내사무소장", "팀장, 실 산하 원장", "사무소장", "담당", "기안", "별도 지침", "비고"]
APPROVERS = ["이사장", "본부장", "처장", "실·원·센터·국내사무소장", "팀장·실 산하 원장", "사무소장"]
DRAFTERS = {"본": "본사", "사": "사무소", "본/사": "본사·사무소"}


def clean(value):
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", unicodedata.normalize("NFC", value or ""))).strip()


def row_digest(row, remark=None):
    """Fingerprint one row; a stored row keeps its shared remark under remark_id."""
    remark = row.get("remark", "") if remark is None else remark
    fields = [row["category"], row["work"], row["unit"], row["detail"],
              *[f"{a['role']}:{a['mark']}" for a in row["approvers"]],
              row["owner_mark"], row["drafter_mark"], row["guideline"], remark]
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def column_index(ref):
    letters = re.match(r"[A-Z]+", ref).group()
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def read_sheet(xlsx_bytes):
    book = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    shared = []
    if "xl/sharedStrings.xml" in book.namelist():
        for item in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in item.iter(f"{{{NS['m']}}}t")))
    sheets = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if len(sheets) != 1:
        raise ValueError(f"Expected one worksheet, found {len(sheets)}")
    root = ET.fromstring(book.read(sheets[0]))
    grid = {}
    for row in root.iter(f"{{{NS['m']}}}row"):
        r = int(row.get("r"))
        for cell in row.findall("m:c", NS):
            value = cell.find("m:v", NS)
            inline = cell.find("m:is", NS)
            if cell.get("t") == "s" and value is not None:
                text = shared[int(value.text)]
            elif inline is not None:
                text = "".join(t.text or "" for t in inline.iter(f"{{{NS['m']}}}t"))
            else:
                text = value.text if value is not None else ""
            grid[(r, column_index(cell.get("r")))] = text or ""
    for merged in root.iter(f"{{{NS['m']}}}mergeCell"):
        start, end = merged.get("ref").split(":")
        r1, r2 = int(re.search(r"\d+", start).group()), int(re.search(r"\d+", end).group())
        c1, c2 = column_index(start), column_index(end)
        value = grid.get((r1, c1), "")
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                grid[(r, c)] = value
    last = max(r for r, _ in grid)
    return [[grid.get((r, c), "") for c in range(len(HEADER))] for r in range(1, last + 1)]


def extract(zip_bytes):
    if hashlib.sha256(zip_bytes).hexdigest() != ZIP_SHA256:
        raise ValueError("ALIO zip differs from the reviewed 2024.02.13 file")
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes), metadata_encoding="cp949")
    members = [i for i in archive.infolist() if i.filename.lower().endswith(".xlsx")]
    if len(members) != 1:
        raise ValueError("Expected exactly one annex spreadsheet")
    xlsx = archive.read(members[0])
    sheet = read_sheet(xlsx)
    header_at = next(i for i, row in enumerate(sheet) if [clean(v) for v in row] == HEADER)
    rows, remarks = {}, {}
    for number, raw in enumerate(sheet[header_at + 1:], start=header_at + 2):
        cells = [clean(v) for v in raw]
        category, work, unit, detail = cells[:4]
        if not detail or detail == category:  # section headings span the whole row
            continue
        row = {"sheet_row": number, "category": category, "work": work, "unit": unit, "detail": detail,
               "approvers": [{"role": role, "mark": mark} for role, mark in zip(APPROVERS, cells[4:10]) if mark],
               "owner_mark": cells[10], "drafter_mark": cells[11], "guideline": cells[12], "remark": cells[13]}
        row["drafter"] = DRAFTERS.get(row["drafter_mark"], "")
        row["sha256"] = row_digest(row)
        if row["remark"]:
            row["remark_id"] = remarks.setdefault(row.pop("remark"), f"m{len(remarks) + 1}")
        else:
            row.pop("remark")
        rows[f"r{number}"] = row
    return {"source": "위임전결규정", "label": "[별표 1-2] 단위업무별 위임전결 세부사항",
            "revision": "2024.02.13", "status": "outdated_reference",
            "caveat": ("현행 위임전결규정(2025.12.31 개정)의 별표 1-2는 '별도 파일 참조'로만 공개되어 있습니다. "
                       "ALIO에 마지막으로 공개된 2024.02.13 개정 표를 참고로 보여 주며, 그 뒤 두 차례 개정으로 달라졌을 수 있습니다."),
            "origin": {"page_url": PAGE_URL, "file_no": FILE_NO, "file_name": FILE_NAME, "zip_sha256": ZIP_SHA256,
                       "member": unicodedata.normalize("NFC", members[0].filename),
                       "xlsx_sha256": hashlib.sha256(xlsx).hexdigest()},
            "drafter_legend": DRAFTERS,
            "remarks": {key: text for text, key in remarks.items()}, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--zip", type=Path)
    group.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        sys.path.insert(0, str(ROOT))
        import alio_sync
        data, _ = alio_sync.http_get(f"{alio_sync.BASE}/download/rulefiledown.json?fileNo={FILE_NO}",
                                     referer=alio_sync.LIST_REFERER, binary=True)
    else:
        data = args.zip.read_bytes()
    annex = extract(data)
    OUT.write_text(json.dumps(annex, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"위임전결 별표 1-2 (2024.02.13): {len(annex['rows'])}개 행 → {OUT.name}")


if __name__ == "__main__":
    main()
