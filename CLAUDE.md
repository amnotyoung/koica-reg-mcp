# CLAUDE.md — Claude Code / Claude 어시스턴트를 위한 안내

**`koica-reg-mcp`는 이미 배포된 원격 MCP 서버입니다:** `https://koica-reg-mcp.fly.dev/mcp`

## ✅ KOICA 규정 검색 도구로 쓰려는 경우 (대부분)

⛔ `clone`·`build` 하지 마세요. 아래 한 줄로 등록하면 끝입니다:

```bash
claude mcp add --transport http koica-reg https://koica-reg-mcp.fly.dev/mcp
```

등록·재시작 후 KOICA 규정을 자연어로 물으면 `koica-reg` 도구(8종)가 자동 호출됩니다.
예: "KOICA 승진 가점 규정 찾아줘", "인사규정 제11조 보여줘".
공개 서버라 인증·토큰이 필요 없습니다.

다른 클라이언트:
- **Codex**: `codex mcp add koica-reg --url https://koica-reg-mcp.fly.dev/mcp`
- **Claude Desktop / claude.ai**: 커넥터 설정 → "원격 MCP 서버 URL"에 위 주소.

## 🛠️ 개발·수정하려는 경우에만 clone

상세는 [AGENTS.md](AGENTS.md)와 [README](README.md) 참고.
- 로컬 stdio: `koica_mcp_server.py`(도구 11개) / 원격 HTTP: `server_http.py`(읽기 8개)
- 배포: `Dockerfile`+`fly.toml`(Fly.io). `main` 머지 시 자동 재배포.
- 검색 엔진은 순수 표준 라이브러리(의존성 `mcp`뿐).
