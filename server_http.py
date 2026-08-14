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
from usage_metrics import start_metrics_server

# host/port 는 컨테이너/클라우드에서 주입된다. Fly.io 는 PORT 환경변수를 넘긴다.
#
# stateless_http=True: 상태 유지 모드에서는 initialize 요청마다 세션 전송체가
# 만들어지고, 클라이언트가 DELETE를 보내지 않으면 프로세스가 끝날 때까지 회수되지
# 않는다(현 SDK의 세션 유휴 타임아웃 기본값은 None이고 FastMCP는 이 값을 노출하지
# 않는다). 인증이 없는 공개 서버라 initialize 반복만으로 512MB 머신의 메모리를
# 고갈시킬 수 있다. 노출된 도구 8종은 모두 세션 상태가 없는 단발 조회이므로
# 요청마다 새 전송체를 쓰고 즉시 버리는 편이 안전하고 동작도 동일하다.
mcp = FastMCP(
    "koica-reg",
    instructions=SERVER_INSTRUCTIONS,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
)

# 읽기 전용 도구만 등록. 관리 도구(update/sync)와 find_questions(출처 불명 데이터) 제외.
register_tools(mcp, include_admin=False, include_questions=False)


if __name__ == "__main__":
    metrics_port = os.environ.get("KOICA_METRICS_PORT")
    if metrics_port:
        start_metrics_server(port=int(metrics_port))
    mcp.run(transport="streamable-http")
