"""KOICA 규정 MCP 서버 — 원격 HTTP(streamable-http) 엔트리포인트.

Fly.io 등 클라우드에 배포해 URL 하나로 접속하는 원격 MCP 서버.
로컬 stdio 버전은 koica_mcp_server.py 를 사용하세요.

원격판은 읽기 전용 도구만 노출합니다(update / sync_from_alio 제외) — 원격에서
누구든 서버측 git pull·ALIO 크롤링을 유발하지 못하도록 관리 도구를 뺍니다.
데이터 최신화는 GitHub Actions 주간 동기화 + 재배포로 처리합니다.

클라이언트 연결 (claude.ai / 데스크톱 "커스텀 커넥터 추가"):
  원격 MCP 서버 URL 칸에  https://<앱이름>.fly.dev/mcp  를 입력.

로컬 확인:
  PORT=8080 python server_http.py
  → http://127.0.0.1:8080/mcp 로 streamable-http 서버 기동.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from koica_mcp_server import register_tools, SERVER_INSTRUCTIONS

# host/port 는 컨테이너/클라우드에서 주입된다. Fly.io 는 PORT 환경변수를 넘긴다.
mcp = FastMCP(
    "koica-reg",
    instructions=SERVER_INSTRUCTIONS,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
)

# 읽기 전용 도구만 등록. 관리 도구(update/sync)와 find_questions(출처 불명 데이터) 제외.
register_tools(mcp, include_admin=False, include_questions=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
