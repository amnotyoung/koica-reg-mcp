# AGENTS.md — AI 에이전트·코딩 어시스턴트를 위한 안내

**`koica-reg-mcp`는 이미 배포된 원격 MCP 서버입니다.**
원격 엔드포인트: `https://koica-reg-mcp.fly.dev/mcp` (streamable HTTP, 공개·인증 없음)

---

## ✅ 이 저장소를 "사용"하려는 경우 — 대부분 여기에 해당

KOICA 규정 검색 도구로 쓰고 싶은 거라면:

⛔ **`git clone` / `pip install` / `build` 하지 마세요.** 서버는 이미 원격에서 돌고 있습니다.
사용자의 MCP 클라이언트에 아래 한 줄로 등록만 하면 됩니다.

**Codex**
```bash
codex mcp add koica-reg --url https://koica-reg-mcp.fly.dev/mcp
```

**Claude Code**
```bash
claude mcp add --transport http koica-reg https://koica-reg-mcp.fly.dev/mcp
```

**Claude Desktop / claude.ai (웹)**
설정 → 커넥터 → *커스텀 커넥터 추가* → "원격 MCP 서버 URL"에 `https://koica-reg-mcp.fly.dev/mcp` 입력.

등록·재시작 후 규정을 자연어로 물으면 됩니다. 예:
- "KOICA 승진 가점 규정 찾아줘"
- "인사규정 제11조 본문 보여줘"
- "이 보고서에 인용된 조문이 실재하는지 검증해줘"

제공 도구 8종: `search_regulation`, `get_article`, `verify_citation`,
`find_references`, `compliance_radar`, `list_attachments`, `get_attachment`,
`list_sources`. 공개 서버라 토큰·헤더가 필요 없습니다.

데이터는 ALIO 주간 자동 동기화로 서버가 최신을 유지합니다 — 커넥터 사용자가
실행할 동기화·업데이트 명령은 없습니다.

---

## 🛠️ 이 저장소를 "개발·수정·기여"하려는 경우에만 clone

```bash
git clone https://github.com/amnotyoung/koica-reg-mcp.git
cd koica-reg-mcp
pip install -r requirements.txt
python3 semantic_search.py download-model
python3 koica_search.py build --strict-semantic
```

- `koica_mcp_server.py` — 로컬 stdio 서버 (도구 11개 = 원격 8개 + 관리·개발용 3개: `update`·`sync_from_alio`·`find_questions`)
- `server_http.py` — 원격 HTTP(streamable-http) 서버 (위 8개 그대로. 상태를 바꾸는 도구는 노출하지 않음)
- `Dockerfile` + `fly.toml` — Fly.io 배포. **`main`에 머지되면 GitHub Actions가 자동 재배포**합니다.
- 검색 엔진은 기존 IDF 키워드 점수와 로컬 `intfloat/multilingual-e5-small` ONNX 의미 유사도를 결합합니다. 외부 임베딩 API나 API 키는 쓰지 않습니다.
- 의미 모델은 고정 리비전·SHA-256을 검증해 사용자 캐시에 받고, `numpy`·`onnxruntime`·`sentencepiece`로 추론합니다. 배포 경로는 `KOICA_SEMANTIC_MODEL_DIR`로 고정할 수 있습니다.
- `data/index.json`, `data/semantic_vectors.npy`, `data/semantic_meta.json`은 gitignore된 빌드 산출물이며 Docker와 CI에서는 `--strict-semantic`으로 함께 생성·검증합니다.

---

## 한 줄 요약

**규정을 검색·활용하려는 목적 → 원격 URL 등록(clone 금지).
코드를 고치려는 목적 → clone.**
