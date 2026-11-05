"""Lossless navigation sections over the checked-in Markdown extraction."""
import re


def document_sections(text):
    starts = [(0, "문서 정보", "main")]
    kind = "main"
    offset = 0
    for line in text.splitlines(keepends=True):
        clean = re.sub(r"^#{1,6}\s*", "", line.strip()).strip("*")
        if re.match(r"부\s*칙(?:\s|<|$)", clean):
            kind = "supplementary"
            label = clean
        elif re.match(r"\[\s*별(?:표|지)", clean):
            kind = "attachment"
            label = clean
        elif kind != "attachment" and re.match(r"제\s*\d+\s*조", clean):
            match = re.match(r"(제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^)]*\))?)", clean)
            label = match[1] if match else clean
        elif re.match(r"^#{1,3}\s+제\d+(?:편|장|절)", line):
            label = clean
        else:
            offset += len(line)
            continue
        if offset:
            starts.append((offset, label[:160], kind))
        offset += len(line)
    return [{"id": f"section-{i}", "label": label, "kind": kind,
             "text": text[start:starts[i + 1][0] if i + 1 < len(starts) else len(text)]}
            for i, (start, label, kind) in enumerate(starts)]
