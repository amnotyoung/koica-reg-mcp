# AGENTS.md — AI 에이전트·코딩 어시스턴트를 위한 안내

**`koica-reg-mcp`는 원격 MCP와 로컬 MCP 두 가지 사용 방식을 공식 지원합니다.**

- 원격 엔드포인트: `https://koica-reg-mcp.fly.dev/mcp` (streamable HTTP, 공개·인증 없음)
- 로컬 서버: `koica_mcp_server.py` (stdio, 사용자 PC에서 실행)

---

## ✅ 이 저장소를 "사용"하려는 경우

### 옵션 A — 원격 MCP(기본·설치 불필요)

설치와 사용자별 업데이트가 필요 없으면 원격 MCP를 권장합니다. 이 방식을 선택한 사용자는 `git clone` / `pip install` / `build`를 할 필요가 없습니다.

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

### 옵션 B — 로컬 MCP(원격 서버 없이 실행)

사용자 PC에서 검색 엔진과 의미 모델을 직접 실행하려면 저장소를 clone하고 로컬 stdio 서버를 등록합니다.

```bash
git clone https://github.com/amnotyoung/koica-reg-mcp.git
cd koica-reg-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python semantic_search.py download-model
python koica_search.py build --strict-semantic
```

**Claude Code**
```bash
claude mcp add --transport stdio koica-reg -- /absolute/path/koica-reg-mcp/.venv/bin/python /absolute/path/koica-reg-mcp/koica_mcp_server.py
```

**Codex**
```bash
codex mcp add koica-reg -- /absolute/path/koica-reg-mcp/.venv/bin/python /absolute/path/koica-reg-mcp/koica_mcp_server.py
```

Claude Desktop JSON과 Windows 경로 예시는 [README의 로컬 MCP 설치](README.md#로컬-mcp-설치)를 따릅니다. 로컬 설치본은 약 400~500MB의 저장공간을 사용하고, 의미 검색 시 CPU 코어 1개를 짧게 사용합니다. GPU와 외부 임베딩 API는 필요 없습니다.

로컬 사용자는 `update` 또는 `sync_from_alio`로 자기 설치본을 갱신해야 합니다. 브라우저 전용 클라이언트는 로컬 stdio 프로세스를 직접 실행할 수 없으므로 원격 MCP를 사용합니다.

---

## 🛠️ 이 저장소를 개발·수정·기여하려는 경우

- `koica_mcp_server.py` — 로컬 stdio 서버 (도구 11개 = 원격 8개 + 관리·개발용 3개: `update`·`sync_from_alio`·`find_questions`)
- `server_http.py` — 원격 HTTP(streamable-http) 서버 (위 8개 그대로. 상태를 바꾸는 도구는 노출하지 않음)
- `Dockerfile` + `fly.toml` — Fly.io 배포. **`main`에 머지되면 GitHub Actions가 자동 재배포**합니다.
- 검색 엔진은 기존 IDF 키워드 점수와 로컬 `intfloat/multilingual-e5-small` ONNX 의미 유사도를 결합합니다. 외부 임베딩 API나 API 키는 쓰지 않습니다.
- 의미 모델은 고정 리비전·SHA-256을 검증해 사용자 캐시에 받고, `numpy`·`onnxruntime`·`sentencepiece`로 추론합니다. 배포 경로는 `KOICA_SEMANTIC_MODEL_DIR`로 고정할 수 있습니다.
- `data/index.json`, `data/semantic_vectors.npy`, `data/semantic_meta.json`은 gitignore된 빌드 산출물이며 Docker와 CI에서는 `--strict-semantic`으로 함께 생성·검증합니다.

---

## 한 줄 요약

**간편하게 사용 → 원격 URL 등록.
원격 서버 없이 사용하거나 코드를 수정 → clone 후 로컬 MCP 등록.**
