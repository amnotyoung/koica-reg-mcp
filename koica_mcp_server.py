"""KOICA 규정 MCP 서버 (로컬 stdio 엔트리포인트).

Claude Desktop / Cursor / Windsurf 등 MCP 호환 클라이언트에서 로컬 실행.
원격 HTTP 배포는 server_http.py 를 사용합니다 (읽기 도구만 노출).

도구 정의는 register_tools() 한 곳에 모아 두 엔트리포인트(로컬 stdio /
원격 HTTP)가 공유합니다. include_admin / include_questions 플래그로 도구
노출을 제어합니다.

연결 (Claude Desktop):
  ~/Library/Application Support/Claude/claude_desktop_config.json
  {
    "mcpServers": {
      "koica-reg": {
        "command": "/opt/anaconda3/bin/python3",
        "args": ["/절대경로/koica-reg-mcp/koica_mcp_server.py"]
      }
    }
  }
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

import koica_search as ks


def register_tools(mcp: FastMCP, include_admin: bool = True,
                   include_questions: bool = True) -> None:
    """FastMCP 인스턴스에 도구를 등록한다.

    Args:
        mcp: 대상 FastMCP 서버.
        include_admin: True면 관리 도구(update, sync_from_alio)까지 등록(로컬 stdio용).
            False면 읽기 전용 도구만 등록(원격 HTTP 배포용) — 원격에서 아무나
            서버측 git pull·ALIO 크롤링을 유발하지 못하도록 관리 도구를 제외한다.
        include_questions: True면 find_questions(시험문제 검색)를 등록. 원격 공개
            배포에서는 출처 불명 questions.json을 노출하지 않도록 False로 둔다.
    """

    @mcp.tool()
    def search_regulation(
        query: str,
        category: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 10,
        fuzzy: bool = False,
        include_attachments: bool = False,
    ) -> list[dict]:
        """KOICA 현행 규정을 조문 단위로 검색.

        Args:
            query: 자연어 검색어 (예: "인사위원회 구성")
            category: 규정 유형 필터 (규정/시행세칙/지침/기준/정관). 대부분은
                source(규정명)나 전문검색이 더 유용하며, 유형으로 좁힐 때만 사용.
            source: 규정명 부분일치 (예: "인사규정") — 가장 자주 쓰는 필터
            limit: 반환 결과 수 (기본 10)
            fuzzy: 음절 bi-gram 부분 매칭 활성화 (기본 False). 정확 매칭이
                없을 때 토큰을 2-gram으로 쪼개 부분 매칭 점수를 부여한다.
                "사례비"로 검색해 "사례금"이 본문에 있는 경우를 잡을 때 유용.
            include_attachments: 별표·별지도 검색 대상에 포함 (기본 False).
                행정처분 기준표·서식 등을 찾을 때만 켜세요. 일반 조문 검색에서는
                별표·별지가 결과 품질을 떨어뜨릴 수 있어 기본은 OFF.

        Returns:
            결과 배열. 항목의 type 필드로 "article"/"attachment" 구분.
            article: source/article/article_title/snippet/citation/score
            attachment: source/kind/label/title/snippet/citation/score
        """
        return ks.search(
            query,
            category=category,
            source=source,
            limit=limit,
            fuzzy=fuzzy,
            include_attachments=include_attachments,
        )

    @mcp.tool()
    def list_attachments(
        source: Optional[str] = None,
        category: Optional[str] = None,
        kind: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        """규정의 별표·별지 목록 (행정처분 기준표, 서식 등).

        별표·별지는 본문 조문과 분리되어 별도 인덱싱됩니다. 기본 검색
        (search_regulation)은 본문 조문만 대상이며, 별표·별지를 함께 검색하려면
        search_regulation의 include_attachments=True 옵션을 사용하세요.

        Args:
            source: 규정명 부분일치 (예: "감사규정 시행세칙")
            category: 규정 유형 필터 (규정/시행세칙/지침/기준/정관)
            kind: "별표" 또는 "별지"로만 필터
            include_deleted: 본문에 <삭제 …> 메타가 있는 항목 포함 여부 (기본 False)
        """
        return ks.list_attachments(source=source, category=category, kind=kind, include_deleted=include_deleted)

    @mcp.tool()
    def get_attachment(source: str, label: str) -> list[dict]:
        """별표·별지 본문 전체 조회.

        Args:
            source: 규정명 부분일치 (예: "민관협력사업")
            label: 별표·별지 라벨. "[별표 1]", "별표 1", "1" 등 자유 형식.
                공백·괄호 무시하고 정규화 매칭됨.
        """
        return ks.get_attachment(source, label)

    @mcp.tool()
    def get_article(source: str, article: str) -> list[dict]:
        """규정명·조문 번호로 조문 본문 전체 조회.

        Args:
            source: 규정명 부분일치 (예: "인사규정", "국별협력사업")
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
    def find_references(source: str, article: str, limit: int = 20,
                        include_mermaid: bool = False) -> dict:
        """대상 조문의 정방향·역방향 인용 관계 그래프.

        - outgoing: 이 조문이 인용한 다른 조문 (cross_regulation / same_regulation / external)
        - incoming: 다른 조문이 이 조문을 인용한 곳

        KOICA 규정은 모법↔시행세칙↔지침이 촘촘히 얽혀 있어, 조문 하나를
        참조한 다음 어디로 가야 하는지 자동으로 알려줍니다.

        Args:
            source: 규정명 부분일치 (예: "직제규정")
            article: 조문 번호 (예: "제9조", "15의2")
            limit: 각 방향 최대 결과 수 (기본 20)
            include_mermaid: True면 반환값에 "mermaid"(flowchart 코드) 포함.
                claude.ai 등에서 인용망을 바로 시각화할 때 사용.
        """
        return ks.find_references(source, article, limit=limit, include_mermaid=include_mermaid)

    @mcp.tool()
    def compliance_radar(source: Optional[str] = None) -> list[dict]:
        """규정 정비 레이더 — 시행세칙·지침이 모(母)규정 개정에 뒤처졌는지 점검.

        "모규정이 개정됐는데 시행세칙/지침은 아직 옛날 그대로 아닌가?"를 자동 탐지.
        각 하위 규정의 모규정을 이름 규칙/제1조 인용으로 찾아 개정일을 대조하고,
        모규정이 더 최근이면 정비 검토 대상(review_needed)으로 플래그합니다.
        "규정 정비할 것 있나", "개정 뒤처진 세칙 찾아줘" 등으로 요청 시 사용.

        Args:
            source: 특정 규정만 점검(부분일치). 생략 시 전체 정비 필요 목록.

        Returns:
            [{source, type, revision, parent, parent_revision, status, note}, …]
            status: review_needed(모규정이 더 최근) / ok / unknown
        """
        return ks.compliance_radar(source=source)

    @mcp.tool()
    def list_sources(category: Optional[str] = None) -> list[dict]:
        """인덱싱된 KOICA 현행 규정 목록.

        Args:
            category: 규정 유형만 보기 (규정/시행세칙/지침/기준/정관, 선택)

        Returns:
            [{"source": "인사규정", "category": "규정", "revision": "2026.06.12 개정",
              "article_count": 73}, …]
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

    if include_questions:
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

    if not include_admin:
        return

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
    def sync_from_alio(timeout_sec: int = 1200) -> dict:
        """ALIO(alio.go.kr)에서 KOICA 현행 규정을 직접 받아 최신으로 동기화.

        공공기관 경영정보 공개시스템의 KOICA 규정 목록(apbaId=C0146,
        reportFormRootNo=21110)을 조회 → 각 규정의 현행본(최신 개정본) 다운로드 →
        본문 추출 → 인덱스 재빌드까지 한 번에 수행합니다. 사용자가 "코이카 규정
        ALIO에서 최신으로 동기화해줘", "규정 새로 받아와" 처럼 요청할 때 호출합니다.

        주의:
          - 전 규정 다운로드·추출로 수 분이 걸립니다.
          - Node.js/npx가 필요합니다(문서 추출 kordoc 실행). 없으면 오류를 반환하니
            Node 설치 후 재시도하거나 터미널에서 `python3 alio_sync.py`를 실행하세요.
          - 데이터만 갱신되므로 클라이언트 재시작 없이 즉시 반영됩니다.

        Args:
            timeout_sec: 최대 대기 시간(초, 기본 1200). 초과 시 오류 반환.

        Returns:
            동기화 결과 요약(규정 수·조문 수, 실행 로그 tail).
        """
        from pathlib import Path

        script = Path(ks.ROOT) / "alio_sync.py"
        if not script.exists():
            return {"status": "error", "message": f"동기화 스크립트 없음: {script}"}
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(ks.ROOT), capture_output=True, text=True, timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": f"{timeout_sec}초 내 완료되지 않았습니다. 터미널에서 "
                           "'python3 alio_sync.py'로 직접 실행해 주세요.",
            }

        out = (proc.stdout + "\n" + proc.stderr).strip()
        tail = "\n".join(out.splitlines()[-8:])
        lowered = out.lower()
        if "npx" in lowered and ("not found" in lowered or "enoent" in lowered or "command not found" in lowered):
            return {
                "status": "error",
                "message": "Node.js/npx가 필요합니다(kordoc 실행). Node를 설치한 뒤 다시 시도하세요.",
                "output_tail": tail,
            }
        if proc.returncode != 0:
            return {"status": "error", "message": "동기화 실패", "output_tail": tail}

        # 데이터만 바뀌었으므로 인메모리 인덱스 캐시만 무효화 → 재시작 불필요
        ks._INDEX_CACHE = None
        ks._ATTACHMENT_CACHE = None
        arts = ks.load_index()
        return {
            "status": "ok",
            "restart_required": False,
            "source_count": len({a.source for a in arts}),
            "article_count": len(arts),
            "message": f"ALIO 최신 규정으로 동기화 완료 — {len({a.source for a in arts})}개 규정 / "
                       f"{len(arts)}개 조문. 재시작 없이 바로 사용할 수 있습니다.",
            "output_tail": tail,
        }


mcp = FastMCP("koica-reg")
register_tools(mcp, include_admin=True)


if __name__ == "__main__":
    mcp.run()
