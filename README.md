# koica-reg-mcp

**KOICA 규정 38개를 9개 MCP 도구로.** 한국국제협력단 내부 규정·관련 법령을 조문 단위 검색 + 본문 직접 조회 + 인용 검증 + 상호참조 그래프로, Claude Desktop·Cursor·Windsurf 등 MCP 호환 클라이언트에서 바로 사용.

[![MCP](https://img.shields.io/badge/MCP-1.0-blue)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> KOICA 직원이 일하다 "이거 인사규정 몇 조에 있더라?" 싶을 때, AI 어시스턴트에게 자연어로 물어보면 정확한 조문과 본문이 즉시 나옵니다.

---

## 무엇을 해결하나

- **참조 시간 단축** — 40개 규정 PDF를 매번 열어 Ctrl+F 하는 대신, 자연어 한 줄로 조문 단위 검색
- **인용 검증** — 보고서·답변에 들어간 "{규정명} 제N조" 인용이 실제로 존재하는지 자동 교차검증 (LLM 환각 방지)
- **상호참조 자동 추적** — 어떤 조문이 시행세칙·관련 지침의 어디로 연결되는지 양방향 그래프
- **시험 준비 보조** — KOICA 채용·승진 시험 응시자가 자기 정답을 근거 조문으로 검증

---

## 인덱싱된 규정 (38개, 총 1,660개 조문)

| 카테고리 | 규정 수 | 예시 |
|---|---|---|
| 법률/법령 (law) | 5 | 공공기관운영법, 국제개발협력기본법, 한국국제협력단법, 시행령 |
| 인사/복무 (hr) | 10 | 정관, 직제규정, 인사규정·시행세칙, 보수규정, 복무규정, 국외여비규정 등 |
| 사업관리 (project) | 7 | 사업평가·조달계약·시행세칙, 국별협력사업, 사업계획변경 등 |
| 봉사단·연수 (volunteer) | 5 | 해외봉사단파견·시행세칙, 글로벌연수사업·시행세칙 등 |
| 민관협력 (partnership) | 5 | 민관협력사업, 시민사회협력, 인도적지원, 혁신적 개발협력 등 |
| 회계/감사 (finance) | 4 | 감사규정·시행세칙, 회계규정, 해외사무소 예산집행 |
| 경영/기타 (management) | 2 | 임직원 윤리실천규정, 내부통제규정 |

> 참고: 일부 규정(예: 예산집행 매뉴얼, 중장기 경영목표)은 정형 텍스트 추출이 어려워 인덱싱에서 제외되었습니다.

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/amnotyoung/koica-reg-mcp.git
cd koica-reg-mcp
pip install -r requirements.txt
python3 koica_search.py build
```

빌드가 끝나면 `data/index.json`에 1,660개 조문이 인덱싱됩니다.

### 2. CLI로 바로 써보기

```bash
python3 koica_search.py search "인사위원회 구성" --category hr
python3 koica_search.py get "인사규정" "제11조"
python3 koica_search.py verify "공공기관운영법 제8조에 따라 운영위원회를 둔다"
python3 koica_search.py refs "직제규정" "제9조"
```

### 3. Claude Desktop에 연결

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "koica-reg": {
      "command": "/opt/anaconda3/bin/python3",
      "args": ["/절대/경로/koica-reg-mcp/koica_mcp_server.py"]
    }
  }
}
```

> `command`에는 `mcp` 패키지가 설치된 Python의 절대 경로를 적어주세요 (`which python3`로 확인).

Claude Desktop을 **완전 종료(Cmd+Q)** 후 재실행하면 입력창 하단 도구 메뉴에 `koica-reg` 9개 도구가 보입니다.

---

## 사용 예시

```
"KOICA 인사규정 채용 결격사유 알려줘"
  → search_regulation + get_article 자동 호출, 인사규정 제19조 본문 반환

"이 보고서에 인용된 조문들이 실재하는지 검증해줘"
  → verify_citation, 각 인용을 ok/not_found/unknown_source로 분류

"직제규정 제9조를 인용한 다른 규정 다 찾아줘"
  → find_references, incoming 그래프 반환
```

---

## 도구 9개

| 도구 | 입력 | 반환 |
|---|---|---|
| `search_regulation` | `query, category?, source?, limit?, fuzzy?, include_attachments?` | 조문 단위 검색 (`fuzzy`=음절 bi-gram, `include_attachments`=별표·별지 포함) |
| `get_article` | `source, article` | 정확한 조문 본문 전체 |
| `verify_citation` | `text` | 텍스트 내 모든 "{규정} 제N조" 인용의 실재성 검증 |
| `find_references` | `source, article` | 정방향(outgoing) · 역방향(incoming) 인용 관계 |
| `list_attachments` | `source?, category?, kind?, include_deleted?` | 별표·별지 목록 (행정처분 기준표, 서식 등) |
| `get_attachment` | `source, label` | 별표·별지 본문 전체 (예: `"별표 1"`, `"[별지 제3호 서식]"`) |
| `find_questions` | `query?, question_id?` | (선택) 시험문제 데이터 + 근거 조문 자동 매핑 |
| `update` | — | `git pull + build`로 저장소 갱신, 변경 요약 + OS별 재시작 안내 반환 |
| `list_sources` | `category?` | 인덱싱된 규정 목록과 조문 수 |

각 도구는 Claude Desktop UI에서 보거나 `python3 koica_search.py --help`로도 확인 가능합니다.

---

## 데이터 구조

```
data/
├── extracted/                  # 규정별 .md 추출본 (38개)
│   ├── law_*.md
│   ├── hr_*.md
│   ├── project_*.md
│   ├── volunteer_*.md
│   ├── partnership_*.md
│   ├── finance_*.md
│   ├── management_*.md
│   └── raw/                    # 원시 .txt 추출본 (참고용)
├── sources.json                # 카테고리·파일 매핑 메타데이터
└── index.json                  # 빌드 산출물 (gitignored)
```

---

## 업데이트 받기

새 버전(코드 개선·새 도구·규정 추가)이 푸시되면 두 가지 방법 중 하나로 받을 수 있습니다.

### 방법 1: Claude에게 자연어로 요청 (권장)

Claude Desktop 채팅에서:

> "koica 도구 최신으로 업데이트해줘"

→ Claude가 `update` 도구를 호출해 `git pull + 인덱스 재빌드`를 자동 실행. 변경 내용 요약과 함께 필요한 경우 Claude Desktop 재시작 안내가 출력됩니다. 사용자의 OS(macOS / Windows / Linux)에 맞춰 안내 문구가 자동으로 달라집니다.

### 방법 2: 터미널에서 수동

```bash
cd <clone한 경로>/koica-reg-mcp
git pull
python3 koica_search.py build
```

### 공통: Claude Desktop 재시작

`.py` 코드 파일이 바뀐 경우(검색 알고리즘 개선, 새 도구 추가 등)에는 Claude Desktop을 **완전 종료 후 재실행**해야 변경 사항이 반영됩니다.

- **macOS**: Cmd+Q (또는 메뉴바 → Quit)
- **Windows**: 시스템 트레이의 Claude 아이콘 우클릭 → Quit (또는 작업관리자에서 Claude 프로세스 종료 후 재실행)
- **Linux**: 시스템 트레이 아이콘 → Quit

데이터만 변경된 경우(규정 추가/갱신)에는 인덱스 캐시가 자동으로 무효화되므로 재시작 없이 즉시 사용 가능합니다.

---

## 규정 갱신·추가 요청

KOICA 규정은 정기적으로 개정됩니다. 새 개정판이 나오거나 누락된 규정을 추가해야 하는 경우 **Issue에 PDF를 첨부해서 요청**해 주세요. 메인테이너가 직접 텍스트 추출·검증·커밋합니다. 동료가 별도로 변환·빌드할 필요는 없습니다.

- [규정 갱신·추가 요청](../../issues/new?template=regulation-update.md) — PDF 첨부 필수
- [버그·정확도 신고](../../issues/new?template=bug-report.md)

처리가 끝나면 `git pull && python3 koica_search.py build` 한 번이면 최신 인덱스가 반영됩니다.

---

## 함께 쓰면 좋은 도구

KOICA 규정에 자주 등장하는 외부 법령(공공기관운영법, 국제개발협력기본법, 국가공무원법, 청탁금지법 등)을 더 깊이 다루려면 [`korean-law-mcp`](https://github.com/chrisryugj/korean-law-mcp)를 함께 사용하면 좋습니다. 두 MCP를 동시에 활성화하면 Claude가 자연어 질의에서 양쪽을 자동으로 호출해, **한 번 질문에 KOICA 규정 + 한국 법령 통합 답변**을 받을 수 있습니다.

---

## 기술 메모

- **순수 Python + 표준 라이브러리** (의존성: `mcp` 패키지 하나)
- **macOS NFD 자모 분해** 자동 정규화
- **두 가지 추출 포맷 지원**: 마크다운 정형(`### 제N조`) 및 평문 PDF(`제N조(...) ...`)
- **검색 알고리즘**: 한국어 stopword 제거 + 토큰 IDF 가중 substring 매칭
- **인덱스 캐싱**: MCP 서버 시작 시 1,660개 조문을 메모리에 로드, 매 호출 시 재사용

검색 정확도 (questions.json 기반 자동 평가, n=100~150, 시험문제 자연어 쿼리):
- top-1: ~45%
- top-3: ~70%
- top-10: ~80%

> 단순 키워드 검색 시(자연어 질문문보다 깨끗한 쿼리) 정확도는 훨씬 높습니다. 모든 검증 케이스에서 1위 적중.
> v1.4의 별표·별지 분리로 본문 조문 검색의 노이즈가 줄어들어 top-3가 약 13%p 향상되었습니다.

---

## 로드맵

- [x] v1.0 — 조문 단위 검색, 본문 조회, 인용 검증, 상호참조, 시험문제 매핑
- [x] v1.1 — source 토큰 매칭 fallback + 본문 메타 태그 정리
- [x] v1.2 — `update` MCP 도구: 자연어로 "도구 업데이트" 요청 시 git pull + build 자동 실행, OS별 재시작 안내
- [x] v1.3 — 음절 bi-gram 매칭 (`fuzzy=True` 옵션). 정확 매칭이 부족할 때 토큰을 2글자 단위로 쪼개 부분 매칭. "사례비" → "사례금" 변종 케이스 보강
- [x] v1.4 — 별표·별지 분리 인덱싱 (299개 attachment 추출). `list_attachments` / `get_attachment` 도구 + `search_regulation`의 `include_attachments` 옵션. 본문 조문에서 별표 노이즈가 빠지면서 검색 정확도 top-3 58% → 71% 향상.

---

## 라이센스

MIT License. 자세한 내용은 [LICENSE](LICENSE).

KOICA 규정 원문의 저작권은 한국국제협력단에 있습니다. 본 repo의 추출본은 KOICA가 공식 배포한 시험범위 자료를 기반으로 정리한 것이며, 학습·업무 참조 목적의 fair use 범위 안에서 사용해 주세요.
