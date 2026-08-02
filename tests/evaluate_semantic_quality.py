"""Small end-to-end Korean paraphrase gate for the built semantic index.

This is intentionally not named ``test_*.py``: normal unit tests remain model-free.
Docker/CI runs this script only after ``build --strict-semantic`` has produced the
real corpus vectors.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import koica_search as ks  # noqa: E402


CASES = (
    ("아이 때문에 몇 달 회사를 쉬고 싶다", "인사규정 제45조의3"),
    ("주말은 쉬는 날인가", "복무규정 제22조"),
    ("피를 뽑아 기부하러 갈 때 근무로 인정되나", "복무규정 제26조"),
    ("퇴사 후 정산금은 언제 들어오나", "퇴직금규정 제9조"),
    (
        "마감 못 지킨 업체에게 페널티를 물릴 수 있나",
        "대외무상협력사업에 관한 조달 및 계약규정 제30조",
    ),
    (
        "내부고발자 정체를 동료에게 까발려도 되나",
        "공익신고 처리 및 신고자 보호 등에 관한 운영지침 제23조",
    ),
    (
        "신고했다고 인사상 보복해도 되나",
        "공익신고 처리 및 신고자 보호 등에 관한 운영지침 제24조",
    ),
    (
        "상사에게 따돌림과 폭언을 당했는데 누구한테 얘기하나",
        "직장 내 괴롭힘 방지 및 예방 지침 제10조",
    ),
    (
        "기관 자료 보여달라 했는데 언제까지 알려줘야 하나",
        "정보공개 운영규정 제12조",
    ),
    (
        "부모님이 아파서 오래 보살펴야 하는데 일을 쉴 수 있나",
        "인사규정 제45조의2",
    ),
    ("회사를 그만두게 되는 나이는 몇 살인가", "인사규정 제50조"),
    (
        "자료 공개를 거절당했는데 다시 따질 수 있나",
        "정보공개 운영규정 제15조",
    ),
    ("집에서 컴퓨터로 일하는 제도가 있나", "유연근무제 운영지침 제4조"),
    (
        "여러 업체 견적 없이 한 곳에 바로 맡길 수 있는 조건",
        "대외무상협력사업에 관한 조달 및 계약규정 제23조의2",
    ),
    (
        "잘못 없이 납품이 늦어져도 벌금을 내나",
        "대외무상협력사업에 관한 조달 및 계약규정 제30조",
    ),
)


def recall_at_10(mode: str) -> tuple[int, list[str]]:
    hits = 0
    misses: list[str] = []
    for query, expected_citation in CASES:
        citations = {
            row["citation"] for row in ks.search(query, limit=10, mode=mode)
        }
        if expected_citation in citations:
            hits += 1
        else:
            misses.append(f"{query} → {expected_citation}")
    return hits, misses


def main() -> None:
    keyword_hits, _ = recall_at_10("keyword")
    hybrid_hits, hybrid_misses = recall_at_10("hybrid")
    total = len(CASES)
    print(
        f"한국어 의역 Recall@10: keyword={keyword_hits}/{total}, "
        f"hybrid={hybrid_hits}/{total}"
    )
    for miss in hybrid_misses:
        print(f"MISS: {miss}")

    minimum_hits = 12  # 80% of the fixed 15-query paraphrase set
    minimum_gain = 6
    if hybrid_hits < minimum_hits:
        raise SystemExit(f"hybrid Recall@10 미달: {hybrid_hits} < {minimum_hits}")
    if hybrid_hits - keyword_hits < minimum_gain:
        raise SystemExit(
            f"keyword 대비 hybrid 개선폭 미달: {hybrid_hits - keyword_hits} < {minimum_gain}"
        )


if __name__ == "__main__":
    main()
