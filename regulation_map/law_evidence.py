"""Locate document-title citations within the current internal regulations."""
import hashlib
import re
import unicodedata


def title_key(title):
    return re.sub(r"[\s·ㆍ‧]", "", unicodedata.normalize("NFC", title))


# Include an unquoted suffix so 「OO법」 시행령 never counts as citing OO법 itself.
QUOTED = re.compile(r"[「｢『]([^」｣』\n]{1,150})[」｣』](?:\s*(시행령|시행규칙))?")


# Only carry coordinates across adjacent enumerations, never across another title.
_PART = r"(?:제\s*)?\d+\s*"
_ART = r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
_HANG = _PART + "항"
_HO = _PART + r"호(?:\s*의\s*\d+)?"
_MOK = r"[가나다라마바사아자차카타파하]\s*목"
_COORD = re.compile(rf"\s*({_ART})?\s*({_HANG})?\s*({_HO})?\s*({_MOK})?")
_JOIN = re.compile(r"\s*(?:,|·|ㆍ|및|또는|와|과|이나|부터|내지|~)\s*")
_ALIAS = re.compile(r"""^\s*[(（]\s*이하\s*["'“‘][^()（）\n]{1,64}?["'”’]\s*(?:이?라(?:고)?\s*한다\.?)?\s*[)）]""")


def cited_provisions(tail):
    tail = _ALIAS.sub("", tail, count=1)
    previous = [""] * 4
    pos = 0
    while True:
        match = _COORD.match(tail, pos)
        parts = list(match.groups())
        if not any(parts) or (pos == 0 and not parts[0]):
            return
        first = next(i for i, part in enumerate(parts) if part)
        for i in range(first, 4):
            part = re.sub(r"\s+", "", parts[i] or "")
            if i in (1, 2) and part and not part.startswith("제"):
                part = "제" + part
            previous[i] = part
        yield "".join(previous)
        join = _JOIN.match(tail, match.end())
        if not join:
            return
        pos = join.end()


def attach_law_evidence(snapshot, sources, excerpts=None):
    excerpts = excerpts or {}
    for reg, row in snapshot["mappings"].items():
        articles = sources.get(reg, {}).get("articles", [])
        reviewed_lines = {}
        for law, selections in row.get("related_provisions", {}).items():
            if law not in row["refs"]:
                continue
            for selection in selections:
                key = selection["article"], selection["line_sha256"]
                reviewed_lines.setdefault(key, []).append((law, selection))
        names = {}
        for law, original in row["refs"].items():
            for name in (law, original):
                names.setdefault(title_key(name), set()).add(law)
        hits = {}
        for index, article in enumerate(articles):
            for line_no, line in enumerate(article["body"].split("\n")):
                matched = {}
                for match in QUOTED.finditer(line):
                    key = title_key(match[1] + (match[2] or ""))
                    targets = names.get(key, set())
                    if len(targets) == 1 and key != title_key(reg):
                        law, = targets
                        refs = matched.setdefault(law, [])
                        cited_name = (match[1] + (" " + match[2] if match[2] else "")).strip()
                        for provision in cited_provisions(line[match.end():]):
                            excerpt = excerpts.get(title_key(cited_name), {}).get(provision, {})
                            ref = {"title": cited_name, "provision": provision, **excerpt}
                            if ref not in refs:
                                refs.append(ref)
                if reviewed_lines:
                    key = article["article"], hashlib.sha256(line.encode()).hexdigest()
                    direct = {law for law, refs in matched.items() if refs}
                    for law, selection in reviewed_lines.get(key, []):
                        if law in direct:
                            continue
                        title = selection.get("title", law)
                        if "unresolved" in selection:
                            matched.setdefault(law, []).append({
                                "title": title, "unresolved": selection["unresolved"]})
                            continue
                        provision = selection["provision"]
                        excerpt = excerpts[title_key(title)][provision]
                        ref = {"title": title, "provision": provision, "contextual": True, **excerpt}
                        if "note" in selection:
                            ref["note"] = selection["note"]
                        matched.setdefault(law, []).append(ref)
                for law, refs in sorted(matched.items()):
                    hits.setdefault(law, []).append([index, line_no, refs] if refs else [index, line_no])
        row["evidence"] = hits
        # A reviewed explanation depends on the version of its governing article.
        for law, review in list(row.get("reviewed", {}).items()):
            if not any(articles[i]["article"] == review["article"] and
                       hashlib.sha256(articles[i]["body"].encode()).hexdigest() == review["body_sha256"]
                       for i, *_ in hits.get(law, [])):
                del row["reviewed"][law]
    return snapshot
