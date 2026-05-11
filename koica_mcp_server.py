"""KOICA 규정 MCP 서버.

Claude Desktop / Cursor / Windsurf 등 MCP 호환 클라이언트에서 사용.

연결 (Claude Desktop):
  ~/Library/Application Support/Claude/claude_desktop_config.json
  {
    "mcpServers": {
      "koica-reg": {
        "command": "/opt/anaconda3/bin/python3",
        "args": ["/Users/nddn/Documents/Claude/Projects/koica-reg-mcp/koica_mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

import koica_search as ks

mcp = FastMCP("koica-reg")


@mcp.tool()
def search_regulation(
    query: str,
    category: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 10,
    fuzzy: bool = False,
) -> list[dict]:
    """KOICA 규정·법률을 조문 단위로 검색.

    Args:
        query: 자연어 검색어 (예: "인사위원회 구성")
        category: 카테고리 필터 (law/hr/project/volunteer/partnership/finance/management)
        source: 규정명 부분일치 (예: "인사규정")
        limit: 반환 결과 수 (기본 10)
        fuzzy: 음절 bi-gram 부분 매칭 활성화 (기본 False). 정확 매칭이
            없을 때 토큰을 2-gram으로 쪼개 부분 매칭 점수를 부여한다.
            "사례비"로 검색해 "사례금"이 본문에 있는 경우를 잡을 때 유용.
            일반 검색에서는 noise를 만들 수 있으니, 1차 검색 결과가
            비거나 명백히 부족할 때만 fuzzy=True로 재시도하길 권장.

    Returns:
        조문 단위 결과 배열. 각 항목은 source/article/article_title/snippet/citation/score 포함.
    """
    return ks.search(query, category=category, source=source, limit=limit, fuzzy=fuzzy)


@mcp.tool()
def get_article(source: str, article: str) -> list[dict]:
    """규정명·조문 번호로 조문 본문 전체 조회.

    Args:
        source: 규정명 부분일치 (예: "인사규정", "공공기관운영")
        article: 조문 번호 (예: "제11조", "11", "15의2", "제15조의2")

    Returns:
        매칭 조문 배열. body 필드에 전체 본문 포함. 0건이면 빈 배열.
    """
    return ks.get_article(source, article)


@mcp.tool()
def verify_citation(text: str) -> list[dict]:
    """텍스트 안의 모든 '{규정명} 제N조' 인용을 인덱스로 교차검증.

    LLM이 지어낸 가짜 조문(환각)을 잡아낼 때 사용. 각 인용을
    ok / not_found / unknown_source 셋 중 하나로 분류하고,
    not_found인 경우 해당 규정의 실제 조문 범위를 함께 안내.

    Args:
        text: 검증할 한국어 텍스트 (여러 인용이 섞여 있어도 됨)
    """
    return ks.verify_citation(text)


@mcp.tool()
def find_references(source: str, article: str, limit: int = 20) -> dict:
    """대상 조문의 정방향·역방향 인용 관계 그래프.

    - outgoing: 이 조문이 인용한 다른 조문 (cross_regulation / same_regulation / external)
    - incoming: 다른 조문이 이 조문을 인용한 곳

    KOICA 규정은 모법↔시행세칙↔지침이 촘촘히 얽혀 있어, 조문 하나를
    참조한 다음 어디로 가야 하는지 자동으로 알려줍니다.

    Args:
        source: 규정명 부분일치 (예: "직제규정")
        article: 조문 번호 (예: "제9조", "15의2")
        limit: 각 방향 최대 결과 수 (기본 20)
    """
    return ks.find_references(source, article, limit=limit)


@mcp.tool()
def find_questions(
    query: Optional[str] = None,
    question_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """KOICA 시험문제 데이터(280문항)에서 검색하고 근거 조문을 자동 연결.

    Args:
        query: 자연어 검색어 (id 미지정 시 사용)
        question_id: 특정 문항 ID (예: "set_law_1_q01")
        limit: 결과 수 (기본 5)

    Returns:
        문항 + 보기 + 정답 + 해설 + 근거 조문(자동 매핑된 본문 발췌).
    """
    return ks.find_questions(query=query, question_id=question_id, limit=limit)


@mcp.tool()
def update() -> dict:
    """koica-reg-mcp 저장소를 최신으로 갱신합니다 (git pull + 인덱스 재빌드).

    사용자가 "도구 업데이트", "최신으로 받아줘" 같은 자연어로 요청할 때 호출합니다.

    동작:
        1) git pull --ff-only — 원격에서 최신 코드/데이터 받기
        2) python3 koica_search.py build — 인덱스 재빌드
        3) 변경 파일·조문 수 변화·OS별 재시작 안내 반환

    코드 파일(.py)이 바뀐 경우 Claude Desktop 재시작이 필요합니다. 데이터만
    바뀐 경우(규정 추가/갱신)는 재시작 없이 즉시 사용 가능합니다. 반환값의
    restart_required와 restart_instruction을 사용자에게 그대로 전달하세요.
    """
    return ks.self_update()


@mcp.tool()
def list_sources(category: Optional[str] = None) -> list[dict]:
    """인덱싱된 규정·법률 목록.

    Args:
        category: 특정 카테고리만 보기 (선택)

    Returns:
        [{"source": "인사규정", "category": "hr", "revision": "...", "article_count": 84}, …]
    """
    arts = ks.load_index()
    by_src: dict[str, dict] = {}
    for a in arts:
        if category and a.category != category:
            continue
        key = a.source
        if key not in by_src:
            by_src[key] = {
                "source": a.source,
                "category": a.category,
                "revision": a.revision,
                "article_count": 0,
            }
        by_src[key]["article_count"] += 1
    return sorted(by_src.values(), key=lambda x: (x["category"], x["source"]))


if __name__ == "__main__":
    mcp.run()
