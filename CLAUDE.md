# CLAUDE.md — Claude Code / Claude 어시스턴트를 위한 안내

**`koica-reg-mcp`는 원격 MCP와 로컬 MCP 두 가지 사용 방식을 지원합니다.**

## ✅ 옵션 A — 원격 MCP(설치 불필요)

사용자별 설치·업데이트가 필요 없으면 아래 원격 엔드포인트를 등록합니다:

```bash
claude mcp add --transport http koica-reg https://koica-reg-mcp.fly.dev/mcp
```

등록·재시작 후 KOICA 규정을 자연어로 물으면 `koica-reg` 도구(8종)가 자동 호출됩니다.
예: "KOICA 승진 가점 규정 찾아줘", "인사규정 제11조 보여줘".
공개 서버라 인증·토큰이 필요 없습니다.
데이터는 ALIO 주간 자동 동기화로 서버가 최신을 유지합니다 — 커넥터 사용자가 실행할 명령은 없습니다.

다른 클라이언트:
- **Codex**: `codex mcp add koica-reg --url https://koica-reg-mcp.fly.dev/mcp`
- **Claude Desktop / claude.ai**: 커넥터 설정 → "원격 MCP 서버 URL"에 위 주소.

## ✅ 옵션 B — 로컬 MCP(원격 서버 없이 실행)

사용자 PC에서 `koica_mcp_server.py`를 stdio 서버로 실행합니다. Python 3.11 이상, 약 400~500MB의 저장공간이 필요하며 GPU나 외부 임베딩 API는 필요 없습니다.

```bash
git clone https://github.com/amnotyoung/koica-reg-mcp.git
cd koica-reg-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python semantic_search.py download-model
python koica_search.py build --strict-semantic
claude mcp add --transport stdio koica-reg -- "$PWD/.venv/bin/python" "$PWD/koica_mcp_server.py"
```

로컬 설치본은 원격과 같은 검색 도구 8개와 관리용 3개(`update`·`sync_from_alio`·`find_questions`)를 제공합니다. 사용자가 `update` 또는 `sync_from_alio`로 데이터를 갱신해야 하며, 웹 전용 claude.ai에서는 로컬 stdio 대신 원격 MCP를 사용합니다.

Windows·Claude Desktop·Codex 설정은 [README의 로컬 MCP 설치](README.md#로컬-mcp-설치)를 참고합니다.

## 🛠️ 개발·수정하려는 경우

상세는 [AGENTS.md](AGENTS.md)와 [README](README.md) 참고.
- 원격 HTTP: `server_http.py` — 규정 검색·조회·검증에 필요한 8개
- 로컬 stdio: `koica_mcp_server.py` — 도구 11개 = 원격 8개 + 관리·개발용 3개(`update`·`sync_from_alio`·`find_questions`)
- 배포: `Dockerfile`+`fly.toml`(Fly.io). `main` 머지 시 자동 재배포.
- 검색 엔진은 IDF 키워드 + 로컬 `intfloat/multilingual-e5-small` ONNX 의미 검색을 결합합니다. 외부 임베딩 API는 사용하지 않습니다.
- 로컬 빌드: `pip install -r requirements.txt` → `python3 semantic_search.py download-model` → `python3 koica_search.py build --strict-semantic`.
