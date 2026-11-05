"""Regression coverage for provenance-sensitive regulation map extraction."""
import tempfile
import unittest
from pathlib import Path
import json
from copy import deepcopy

import koica_search as ks
from regulation_map.build import ORG, ROOT, build_relations, official_source, parse_departments
from regulation_map.reviews import apply_reviews, digest
from regulation_map.bundle import bundle


def article(source, body, number=1, title="목적"):
    return ks.Article("규정", source, "2026.01.01 개정", "test.md", "",
                      f"제{number}조", number, 0, title, body)


class DepartmentExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.departments = parse_departments(
            (ROOT / "data" / "extracted" / f"시행세칙_{ORG}.md").read_text(encoding="utf-8"))
        cls.by_name = {d["name"]: d for d in cls.departments}

    def test_department_names_are_unique_and_audit_markdown_is_read(self):
        self.assertEqual(len(self.departments), len(self.by_name))
        self.assertEqual(len(self.by_name["감사실"]["duties"]), 10)
        self.assertEqual(self.by_name["감사실"]["division"], "독립부서")

    def test_shared_work_is_propagated_only_across_merged_rows(self):
        for name in ("동남아시아1팀", "동남아시아2팀", "서남아태평양실"):
            shared = [d for d in self.by_name[name]["duties"] if d["shared"]]
            self.assertEqual(len(shared), 5)
            self.assertIn("국별협력사업 기획 및 관리", [d["title"] for d in shared])
            self.assertTrue(any("PCP 검토" in d["text"] for d in shared))
        self.assertFalse(any(d["shared"] for d in self.by_name["ODA연구·교육원"]["duties"]))

    def test_blank_parent_cells_and_three_level_organization(self):
        self.assertEqual(self.by_name["지역사업총괄실"]["path"], ["사업전략처", "지역사업총괄실"])
        self.assertEqual(self.by_name["다자협력총괄팀"]["path"], ["인도지원처", "다자협력인도지원실", "다자협력총괄팀"])
        self.assertEqual(self.by_name["인공지능전환·정보화팀"]["duties"][0]["title"], "전사 AX 통합 전략 수립 및 AX 이행 지원")

    def test_unsupported_input_fails_instead_of_silent_empty_map(self):
        with self.assertRaises(ValueError):
            parse_departments("# 직제규정 시행세칙\n업무분장 없음")


class ExplicitRelationTests(unittest.TestCase):
    def test_reference_direction_and_evidence_are_preserved(self):
        articles = [article("가규정", "「나규정」 제2조에 따른다."), article("나규정", "본문", 2)]
        edges = build_relations(articles, {a.source for a in articles})
        self.assertEqual(len(edges), 1)
        self.assertEqual((edges[0]["from"], edges[0]["to"]), ("가규정", "나규정"))
        self.assertEqual(edges[0]["evidence"][0]["target_article"], "제2조")
        self.assertIn("「나규정」 제2조", edges[0]["evidence"][0]["quote"])

    def test_implementation_title_does_not_create_parent_edge(self):
        articles = [article("가규정", "인사규정 시행세칙 제1조를 따른다."), article("인사규정", "본문"), article("인사규정 시행세칙", "본문")]
        edges = build_relations(articles, {a.source for a in articles})
        self.assertEqual([e["to"] for e in edges], ["인사규정 시행세칙"])

    def test_missing_article_unknown_longer_title_and_suffix_are_not_edges(self):
        articles = [article("가규정", "나규정 제99조. 다른나규정 제1조. 나규정 시행세칙 제1조."), article("나규정", "본문")]
        self.assertEqual(build_relations(articles, {a.source for a in articles}), [])

    def test_document_only_reference_is_not_invented_article(self):
        articles = [article("가규정", "「나 규정」에 따라 처리한다."), article("나규정", "본문")]
        edge = build_relations(articles, {a.source for a in articles})[0]
        self.assertEqual(edge["evidence"][0]["target_article"], "")
        self.assertEqual(edge["kind"], "explicit")


class OfficialSourceTests(unittest.TestCase):
    def test_every_manifest_regulation_links_to_its_alio_page_and_file(self):
        manifest = json.loads((ROOT / "data" / "sources.json").read_text(encoding="utf-8"))
        for entry in manifest["sources"]:
            official = official_source(entry, manifest["list_url"])
            self.assertTrue(official["page_url"].startswith("https://www.alio.go.kr/item/itemBoard"), entry["name"])
            self.assertEqual(official["file_url"], f"https://www.alio.go.kr/download/rulefiledown.json?fileNo={entry['file_no']}")
            self.assertRegex(official["collected_at"], r"^\d{4}-\d{2}-\d{2}$", entry["name"])
            self.assertEqual(official["revision"], entry["revision"])

    def test_missing_manifest_values_are_left_empty_not_guessed(self):
        official = official_source(None, "https://www.alio.go.kr/list")
        self.assertEqual((official["page_url"], official["file_url"], official["collected_at"]), ("", "", ""))
        self.assertEqual(official["list_url"], "https://www.alio.go.kr/list")


class BundleTests(unittest.TestCase):
    def make_site(self, root, app="window.APP=1;"):
        page = (ROOT / "regulation_map" / "index.html").read_text(encoding="utf-8")
        (root / "index.html").write_text(page, encoding="utf-8")
        (root / "styles.css").write_text("body{color:red}", encoding="utf-8")
        for name, body in {"data.js": "window.REGULATION_MAP={};", "map_model.js": "window.M=1;",
                           "reader.js": "window.R=1;", "app.js": app}.items():
            (root / name).write_text(body, encoding="utf-8")

    def test_single_file_inlines_every_asset_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_site(Path(tmp))
            html = bundle(Path(tmp))
        self.assertNotIn('src="', html)
        self.assertNotIn('href="styles.css"', html)
        self.assertIn("body{color:red}", html)
        order = [html.index(x) for x in ["window.REGULATION_MAP", "window.M=1", "window.R=1", "window.APP=1"]]
        self.assertEqual(order, sorted(order))
        self.assertLess(order[-1], html.index("</body>"))
        self.assertEqual(html.count('name="regulation-map-bundle"'), 1)
        self.assertLess(html.index('name="regulation-map-bundle"'), html.index("</head>"))

    def test_source_that_would_close_the_inline_script_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_site(Path(tmp), app="const s='</script>';")
            with self.assertRaises(ValueError):
                bundle(Path(tmp))


class ContentReviewTests(unittest.TestCase):
    def test_reassessed_duties_keep_common_procedures_distinct_from_direct_authority(self):
        catalog = json.loads((ROOT / "regulation_map/content_reviews.json").read_text())
        reassessed = [r for r in catalog["duty_reviews"] if r.get("direct_mapping_status") == "not_established"]
        self.assertEqual(len(reassessed), 7)
        for review in reassessed:
            self.assertTrue(review["link_ids"])
            self.assertNotEqual(review.get("outcome"), "no_direct_basis")
            for key in review["link_ids"]:
                self.assertIn(catalog["links"][key]["relation_basis"],
                              {"direct_component", "related_principle", "common_procedure"})
                self.assertNotEqual(review["priorities"][key]["level"], "core")
        wfk = next(r for r in reassessed if r["department"].endswith("해외봉사총괄팀"))
        self.assertNotIn("기관 간 약정 체결 및 관리 업무 지침", [catalog["links"][key]["source"] for key in wfk["link_ids"]])
        kodabiz = next(r for r in reassessed if r["department"].endswith("동반성장팀"))
        self.assertNotIn("기업성장응답센터 운영지침", [catalog["links"][key]["source"] for key in kodabiz["link_ids"]])

    def setUp(self):
        self.articles = [article("회계규정", "자금의 출납을 관리한다.")]
        self.texts = {ORG: "업무분장 원문", "회계규정": "회계규정 원문"}
        self.departments = [{"id": "재무회계팀", "duties": [
            {"id": "d1", "title": "자금관리", "text": "자금관리"},
            {"id": "d2", "title": "자금 관련 다른 업무", "text": "자금 출납 관리"}]}]
        self.catalog = {"schema_version": 2, "reviewer": "AI 내용검토", "reviewed_at": "2026-11-05", "priority_policy": {},
            "organization_source": ORG,
            "source_snapshots": {s: {"sha256": digest(t), "revision": "2026", "read_articles": ["제1조"]} for s, t in self.texts.items()},
            "links": {"funds": {"source": "회계규정", "role": "자금·출납", "rationale": "자금 출납 절차", "condition": "자금 출납 업무",
                "evidence": [{"source": "회계규정", "article": "제1조", "quote": "자금의 출납", "body_sha256": digest(self.articles[0].body)}]}},
            "duty_reviews": [{"department": "재무회계팀", "duty_id": "d1", "duty_sha256": digest("자금관리"),
                              "link_ids": ["funds"], "priorities": {"funds": {"level": "core", "order": 1, "reason": "자금 출납을 직접 다룸"}},
                              "scope": "자금 출납 부분", "gaps": ["세부 기준 미검토"]}]}

    def apply(self):
        return apply_reviews(self.departments, self.articles, self.texts, self.catalog, [])

    def test_only_authored_duty_is_linked_even_when_other_duty_shares_words(self):
        stats = self.apply()
        self.assertEqual(stats["reviewed_duties"], 1)
        self.assertEqual(self.departments[0]["duties"][0]["links"][0]["source"], "회계규정")
        self.assertEqual(self.departments[0]["duties"][1]["links"], [])
        self.assertEqual(self.departments[0]["duties"][1]["review"]["status"], "unreviewed")

    def test_source_revision_hides_link_without_refreshing_review_hash(self):
        before = deepcopy(self.catalog)
        self.texts["회계규정"] += " 개정"
        self.assertEqual(self.apply()["stale_duties"], 1)
        self.assertEqual(self.departments[0]["duties"][0]["links"], [])
        self.assertEqual(self.catalog, before)

    def test_duty_and_organization_changes_require_review(self):
        self.departments[0]["duties"][0]["text"] += " 새 업무"
        self.assertEqual(self.apply()["stale_duties"], 1)
        self.departments[0]["duties"][0]["text"] = "자금관리"
        self.texts[ORG] += " 조직개편"
        self.assertEqual(self.apply()["stale_duties"], 1)

    def test_fabricated_quote_is_rejected(self):
        self.catalog["links"]["funds"]["evidence"][0]["quote"] = "존재하지 않는 근거"
        with self.assertRaisesRegex(ValueError, "Invalid review evidence"):
            self.apply()

    def test_evidence_gap_is_distinct_from_unreviewed_and_becomes_stale(self):
        review = self.catalog["duty_reviews"][0]
        review.update(link_ids=[], priorities={}, outcome="no_direct_basis", searched_sources=["회계규정"])
        stats = self.apply()
        self.assertEqual(stats["evidence_gap_duties"], 1)
        self.assertEqual(stats["assessed_duties"], 1)
        self.assertEqual(stats["unreviewed_duties"], 1)
        self.texts["회계규정"] += " 개정"
        self.assertEqual(self.apply()["stale_duties"], 1)

    def test_article_outside_reading_scope_is_rejected(self):
        self.catalog["source_snapshots"]["회계규정"]["read_articles"] = []
        with self.assertRaisesRegex(ValueError, "outside the recorded reading scope"):
            self.apply()

    def test_missing_or_invalid_priority_is_not_inferred(self):
        review = self.catalog["duty_reviews"][0]
        priority = review.pop("priorities")
        with self.assertRaisesRegex(ValueError, "authored priority"):
            self.apply()
        review["priorities"] = priority
        priority["funds"]["level"] = "99_percent"
        with self.assertRaisesRegex(ValueError, "Invalid authored priority"):
            self.apply()

    def test_real_catalog_has_valid_evidence_and_no_automatic_fallback(self):
        catalog = json.loads((ROOT / "regulation_map/content_reviews.json").read_text())
        articles, texts = [], {}
        for p in ks.DATA_DIR.glob("*.md"):
            if p.stem.split("_", 1)[-1] not in catalog["source_snapshots"]:
                continue
            parsed, _ = ks.parse_md(p, p.stem.split("_", 1)[0])
            articles += [a for a in parsed if not a.is_supplementary]
            texts[parsed[0].source] = p.read_text()
        departments = parse_departments(texts[ORG])
        relations = build_relations(articles, texts)
        stats = apply_reviews(departments, articles, texts, catalog, relations)
        self.assertEqual(stats["stale_duties"], 0)
        self.assertEqual(stats["orphaned_reviews"], 0)
        self.assertEqual(stats["assessed_duties"], len(catalog["duty_reviews"]))
        self.assertEqual(stats["reviewed_duties"], sum(bool(r["link_ids"]) for r in catalog["duty_reviews"]))
        self.assertEqual(stats["unreviewed_duties"], 0)
        self.assertEqual(stats["reviewed_relations"], len(catalog["relation_reviews"]))
        d = next(d for d in departments if d["name"] == "동남아시아1팀")
        task = next(t for t in d["duties"] if t["title"] == "국별협력사업 기획 및 관리")
        construction = next(l for l in task["links"] if l["role"] == "시설지원 분기")
        self.assertIn("포함될 때만", construction["condition"])
        self.assertEqual(construction["priority"]["level"], "conditional")
        self.assertEqual(task["links"][0]["source"], "국별협력사업 규정")
        self.assertEqual(task["links"][0]["priority"]["level"], "core")
        support = next(d for d in departments if d["name"] == "지역사업총괄실")
        support_task = next(t for t in support["duties"] if t["title"] == "국별협력사업 수행지원")
        same_regulation = next(l for l in support_task["links"] if l["source"] == construction["source"])
        self.assertEqual(same_regulation["priority"]["level"], "core")
        self.assertNotIn("recommendations", task)


if __name__ == "__main__":
    unittest.main()
