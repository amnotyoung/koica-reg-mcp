"""Load authored content reviews; never infer an unreviewed duty's links.

Review fingerprints are authored once, alongside the content assessment. The
normal build only compares them and cannot refresh or bless a changed source.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from regulation_map.delegation_annex import row_digest

PRIORITY_LEVELS = {
    "core": {"label": "먼저 확인", "rank": 1},
    "supporting": {"label": "함께 확인", "rank": 2},
    "conditional": {"label": "해당할 때", "rank": 3},
}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_delegation(catalog, base):
    """Read the extracted annex a catalog declares, or None when it declares none."""
    declared = catalog.get("delegation_reference")
    if declared is None:
        return None
    return json.loads((Path(base) / declared["file"]).read_text(encoding="utf-8"))


def check_delegation(catalog, delegation):
    """Verify the outdated delegation table before any duty may cite it."""
    declared = catalog.get("delegation_reference")
    if declared is None:
        return {}
    if delegation is None or declared.get("xlsx_sha256") != delegation["origin"]["xlsx_sha256"]:
        raise ValueError("Delegation reference does not match the extracted annex")
    for row_id, row in delegation["rows"].items():
        if row_digest(row, delegation["remarks"].get(row.get("remark_id"), "")) != row["sha256"]:
            raise ValueError(f"Delegation annex row changed after extraction: {row_id}")
    return delegation["rows"]


MATCH_LEVELS = {
    "office": "사무소 몫 항목 있음",
    "hq_only": "본사 몫 항목만 있음",
    "related": "가까운 항목만 있음 · 대응 미확정",
    "none": "대응 항목 없음",
}


def office_marked(row):
    return any(a["role"] == "사무소장" for a in row["approvers"]) or row["drafter_mark"] in ("사", "본/사")


def delegation_view(rows, ref):
    note = (ref.get("note") or "").strip()
    ids = ref.get("rows")
    match = ref.get("match")
    if not note or not isinstance(ids, list) or len(set(ids)) != len(ids) or any(i not in rows for i in ids):
        raise ValueError("Invalid delegation reference")
    has_office = any(office_marked(rows[i]) for i in ids)
    if (match not in MATCH_LEVELS or (match == "none") != (not ids) or
            (match == "office" and not has_office) or (match == "hq_only" and has_office)):
        raise ValueError("Delegation match level does not fit its rows")
    return {"match": match, "match_label": MATCH_LEVELS[match], "note": note,
            "rows": [{"id": i, "work": rows[i]["work"], "unit": rows[i]["unit"],
                      "detail": rows[i]["detail"], "sheet_row": rows[i]["sheet_row"],
                      "approvers": rows[i]["approvers"], "drafter": rows[i]["drafter"],
                      "remark_id": rows[i].get("remark_id")} for i in ids]}


def apply_reviews(departments, articles, source_texts, catalog, relations, delegation=None):
    if catalog.get("schema_version") != 2:
        raise ValueError("Unsupported content review schema")
    delegation_rows = check_delegation(catalog, delegation)
    by_article = {}
    for article in articles:
        by_article.setdefault((article.source, article.article), []).append(article)
    hashes = {name: digest(text) for name, text in source_texts.items()}
    snapshots = catalog["source_snapshots"]

    def validate_link(link):
        for field in ("source", "role", "rationale", "condition", "evidence"):
            if not link.get(field):
                raise ValueError(f"Content review missing {field}")
        if link.get("relation_basis") not in (None, "direct_component", "related_principle", "common_procedure"):
            raise ValueError("Unknown relation basis")
        dependencies = {link["source"]} | {e["source"] for e in link["evidence"]}
        if not any(e["source"] == link["source"] for e in link["evidence"]):
            raise ValueError("Target regulation needs its own content evidence")
        if not dependencies.issubset(snapshots):
            raise ValueError("Review dependency lacks a frozen source snapshot")
        if any(hashes.get(s) != snapshots[s]["sha256"] for s in dependencies):
            return False
        for evidence in link["evidence"]:
            matches = by_article.get((evidence["source"], evidence["article"]), [])
            if not any(digest(a.body) == evidence["body_sha256"] and
                       evidence["quote"] and evidence["quote"] in a.body for a in matches):
                raise ValueError(f"Invalid review evidence: {evidence['source']} {evidence['article']}")
            if evidence["article"] not in snapshots[evidence["source"]]["read_articles"]:
                raise ValueError("Evidence article is outside the recorded reading scope")
        return True

    valid_links = {key: validate_link(link) for key, link in catalog["links"].items()}
    tasks = {(d["id"], t["id"]): t for d in departments for t in d["duties"]}
    for task in tasks.values():
        task["links"] = []
        task["review"] = {"status": "unreviewed", "scope": "이 업무의 내용 검토 연결은 아직 작성하지 않았습니다.", "gaps": []}
    seen = set()
    orphaned = 0
    for review in catalog["duty_reviews"]:
        key = (review["department"], review["duty_id"])
        if key in seen:
            raise ValueError(f"Duplicate duty review: {key}")
        seen.add(key)
        priorities = review.get("priorities", {})
        if set(priorities) != set(review["link_ids"]):
            raise ValueError("Every duty link needs an authored priority")
        orders = set()
        for priority in priorities.values():
            order = priority.get("order")
            if (priority.get("level") not in PRIORITY_LEVELS or
                    not isinstance(order, int) or isinstance(order, bool) or order < 1 or
                    order in orders or not priority.get("reason", "").strip()):
                raise ValueError("Invalid authored priority")
            orders.add(order)
        task = tasks.get(key)
        if task is None:
            orphaned += 1
            continue
        fresh_task = (digest(task["text"]) == review["duty_sha256"] and
                      hashes.get(catalog["organization_source"]) == snapshots[catalog["organization_source"]]["sha256"])
        if not review["link_ids"]:
            searched = review.get("searched_sources", [])
            if review.get("outcome") != "no_direct_basis" or not searched or not review.get("gaps"):
                raise ValueError("Empty review needs an explicit evidence gap and search scope")
            if any(s not in snapshots for s in searched):
                raise ValueError("Gap review source lacks a reading snapshot")
            fresh_task = fresh_task and all(hashes.get(s) == snapshots[s]["sha256"] for s in searched)
        links = []
        for link_id in review["link_ids"]:
            if fresh_task and valid_links[link_id]:
                link = deepcopy(catalog["links"][link_id])
                link["priority"] = deepcopy(priorities[link_id])
                link["priority"]["label"] = PRIORITY_LEVELS[link["priority"]["level"]]["label"]
                links.append(link)
        links.sort(key=lambda link: (PRIORITY_LEVELS[link["priority"]["level"]]["rank"], link["priority"]["order"]))
        if len({link["source"] for link in links}) != len(links):
            raise ValueError("Duplicate target regulation in duty review")
        task["links"] = links
        stale_count = len(review["link_ids"]) - len(links)
        task["review"] = {"status": "stale" if stale_count or not fresh_task else "partial" if links else "no_direct_basis",
                          "scope": review["scope"], "gaps": review["gaps"],
                          "reviewed_at": catalog["reviewed_at"], "reviewer": catalog["reviewer"],
                          "stale_links": stale_count, "searched_sources": review.get("searched_sources", []),
                          "direct_mapping_status": review.get("direct_mapping_status")}
        if "delegation_reference" in review:
            if not delegation_rows:
                raise ValueError("Delegation reference needs the extracted annex")
            view = delegation_view(delegation_rows, review["delegation_reference"])
            if fresh_task:
                task["delegation"] = view
    edge_lookup = {(e["from"], e["to"]): e for e in relations}
    for edge in relations:
        edge.pop("content_review", None)
    annotated = 0
    for annotation in catalog.get("relation_reviews", []):
        link = catalog["links"][annotation["link_id"]]
        edge = edge_lookup.get((annotation["from"], annotation["to"]))
        if edge is not None and valid_links[annotation["link_id"]]:
            edge["content_review"] = {"label": annotation["label"], "explanation": annotation["explanation"],
                                      "evidence": deepcopy(link["evidence"]), "reviewed_at": catalog["reviewed_at"]}
            annotated += 1
    delegation_meta = None
    if delegation_rows:
        cited = [t["delegation"] for t in tasks.values() if "delegation" in t]
        remark_ids = {r["remark_id"] for view in cited for r in view["rows"] if r["remark_id"]}
        delegation_meta = {key: delegation[key] for key in ("source", "label", "revision", "caveat")}
        delegation_meta.update({"page_url": delegation["origin"]["page_url"], "file_name": delegation["origin"]["file_name"],
                                "member": delegation["origin"]["member"],
                                "remarks": {k: delegation["remarks"][k] for k in sorted(remark_ids)},
                                "policy": catalog["delegation_reference"]["policy"],
                                "reviewed_at": catalog["delegation_reference"]["reviewed_at"],
                                "referenced_duties": len(cited),
                                "match_counts": {m: sum(v["match"] == m for v in cited) for m in MATCH_LEVELS},
                                "match_labels": MATCH_LEVELS})
    return {"delegation_reference": delegation_meta,
            "reviewed_duties": sum(bool(t["links"]) for t in tasks.values()),
            "assessed_duties": sum(t["review"]["status"] != "unreviewed" for t in tasks.values()),
            "evidence_gap_duties": sum(t["review"]["status"] == "no_direct_basis" for t in tasks.values()),
            "limited_mapping_duties": sum(t["review"].get("direct_mapping_status") == "not_established" for t in tasks.values()),
            "unreviewed_duties": sum(t["review"]["status"] == "unreviewed" for t in tasks.values()),
            "stale_duties": sum(t["review"]["status"] == "stale" for t in tasks.values()),
            "orphaned_reviews": orphaned,
            "duty_links": sum(len(t["links"]) for t in tasks.values()),
            "reviewed_relations": annotated, "reviewer": catalog["reviewer"],
            "reviewed_at": catalog["reviewed_at"],
            "priority_policy": catalog["priority_policy"],
            "reading_scope": [{"source": s, "revision": v["revision"], "articles": v["read_articles"]}
                              for s, v in snapshots.items() if v["read_articles"]]}
