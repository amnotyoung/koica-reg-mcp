# koica-reg-mcp

**KOICA 현행 규정 149개를 10개 MCP 도구로.** 한국국제협력단 내부 규정을 조문 단위 검색 + 본문 직접 조회 + 인용 검증 + 상호참조 그래프로, Claude Desktop·Cursor·Windsurf 등 MCP 호환 클라이언트에서 바로 사용. 규정 원본은 [ALIO(공공기관 경영정보 공개시스템)](https://www.alio.go.kr/item/itemOrganList.do?apbaId=C0146&reportFormRootNo=21110)에서 **자동 동기화**됩니다.

[![MCP](https://img.shields.io/badge/MCP-1.0-blue)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> KOICA 직원이 일하다 "이거 인사규정 몇 조에 있더라?" 싶을 때, AI 어시스턴트에게 자연어로 물어보면 정확한 조문과 본문이 즉시 나옵니다.

---

## 무엇을 해결하나

- **참조 시간 단축** — 149개 규정을 매번 열어 Ctrl+F 하는 대신, 자연어 한 줄로 조문 단위 검색
- **항상 현행본** — ALIO의 KOICA 규정 목록을 주기적으로 자동 수집해 **최신 개정본**만 인덱싱
- **인용 검증** — 보고서·답변에 들어간 "{규정명} 제N조" 인용이 실제로 존재하는지 자동 교차검증 (LLM 환각 방지)
- **상호참조 자동 추적** — 어떤 조문이 시행세칙·관련 지침의 어디로 연결되는지 양방향 그래프
- **시험 준비 보조** — KOICA 채용·승진 시험 응시자가 자기 정답을 근거 조문으로 검증

---

## 인덱싱 범위 (현행 규정 149개, 약 3,831개 조문)

ALIO의 KOICA 규정 목록(`apbaId=C0146`)에 게시된 **현행 규정 전체**를 자동 수집합니다. 별도의 "분야" 분류 대신, 규정명으로 곧바로 찾도록 설계했습니다 — 주 사용 축은 **규정명(`source`) + 본문 전문검색**입니다.

| 규정 유형 | 규정 수 | 예시 |
|---|---|---|
| 규정 | 49 | 인사·복무·보수·직제규정, 회계규정, 국별협력사업 규정, 조달 및 계약규정 … |
| 지침 | 86 | 채용업무처리지침, 근무성적평가지침, 환경·사회 세이프가드 이행지침 … |
| 시행세칙 | 11 | 인사규정 시행세칙, 직제규정 시행세칙, 글로벌연수사업 규정 시행세칙 … |
| 정관·기준·세칙 | 3 | 한국국제협력단 정관, 오다(ODA)전문가 자격관리 기준 … |

> 규정 유형은 규정명에서 자동 도출되며 `category` 필터로 쓸 수 있으나(선택), 대부분의 질의는 규정명·본문 검색으로 충분합니다.
> 외부 법령(공공기관운영법·국제개발협력기본법·국가공무원법 등)은 KOICA 규정이 아니라 이 도구가 다루지 않습니다. 함께 인용되는 외부 법령은 [`korean-law-mcp`](https://github.com/chrisryugj/korean-law-mcp)를 병행하세요 (아래 "함께 쓰면 좋은 도구").

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/amnotyoung/koica-reg-mcp.git
cd koica-reg-mcp
pip install -r requirements.txt
python3 koica_search.py build
```

빌드가 끝나면 `data/index.json`에 약 3,831개 조문 + 1,312개 별표·별지가 인덱싱됩니다.

### 2. CLI로 바로 써보기

```bash
python3 koica_search.py search "인사위원회 구성" --source 인사규정
python3 koica_search.py get "인사규정" "제11조"
python3 koica_search.py verify "인사규정 제11조에 따라 인사위원회를 둔다"
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

Claude Desktop을 **완전 종료(Cmd+Q)** 후 재실행하면 입력창 하단 도구 메뉴에 `koica-reg` 도구가 보입니다.

### 4. Codex에 연결 (선택)

```bash
codex mcp add koica-reg -- /opt/anaconda3/bin/python3 /절대/경로/koica-reg-mcp/koica_mcp_server.py
codex mcp list
```

등록 후 Codex를 재시작하면 `koica-reg` 네임스페이스의 도구가 노출됩니다.

---

## 사용 예시

```
"KOICA 인사규정 채용 결격사유 알려줘"
  → search_regulation + get_article 자동 호출, 인사규정 제19조 본문 반환

"이 보고서에 인용된 조문들이 실재하는지 검증해줘"
  → verify_citation, 각 인용을 ok/not_found/unknown_source로 분류

"직제규정 제9조를 인용한 다른 규정 다 찾아줘"
  → find_references, incoming 그래프 반환

"코이카 규정 ALIO에서 최신으로 동기화해줘"
  → sync_from_alio, alio.go.kr에서 현행본을 다시 받아 재인덱싱
```

---

## 도구 10개

| 도구 | 입력 | 반환 |
|---|---|---|
| `search_regulation` | `query, category?, source?, limit?, fuzzy?, include_attachments?` | 조문 단위 검색 (`fuzzy`=음절 bi-gram, `include_attachments`=별표·별지 포함) |
| `get_article` | `source, article` | 정확한 조문 본문 전체 |
| `verify_citation` | `text` | 텍스트 내 모든 "{규정} 제N조" 인용의 실재성 검증 |
| `find_references` | `source, article` | 정방향(outgoing) · 역방향(incoming) 인용 관계 |
| `list_attachments` | `source?, category?, kind?, include_deleted?` | 별표·별지 목록 (행정처분 기준표, 서식 등) |
| `get_attachment` | `source, label` | 별표·별지 본문 전체 (예: `"별표 1"`, `"[별지 제3호 서식]"`) |
| `find_questions` | `query?, question_id?` | (선택) 시험문제 데이터 + 근거 조문 자동 매핑 |
| `sync_from_alio` | `timeout_sec?` | **ALIO에서 현행 규정 재수집 → 다운로드 → 추출 → 재빌드** |
| `update` | — | `git pull + build`로 저장소 갱신, 변경 요약 + OS별 재시작 안내 반환 |
| `list_sources` | `category?` | 인덱싱된 규정 목록과 조문 수 |

> `category`는 규정 유형(규정/시행세칙/지침/기준/정관/법령) 필터입니다. 대부분은 `source`(규정명)나 본문 검색이 더 정확합니다.

---

## 현행성 유지 — ALIO 자동 동기화

규정은 정기적으로 개정됩니다. 이 저장소는 세 갈래로 최신을 유지합니다.

### ① 자동 (권장) — GitHub Actions 주간 동기화

`.github/workflows/alio-sync.yml`이 **매주** ALIO를 다시 조회해 현행본을 받아오고, 변경이 있으면 저장소에 자동 커밋합니다. 사용자는 아무것도 안 해도 저장소가 최신을 따라갑니다. → 받을 때는 아래 `update` 한 번.

### ② 사용자 직접 — `sync_from_alio` 도구 / CLI

지금 당장 최신이 필요하면 Claude에게 "코이카 규정 ALIO에서 최신으로 동기화해줘"라고 요청(`sync_from_alio` 도구)하거나, 터미널에서:

```bash
python3 alio_sync.py          # ALIO 조회→다운로드→추출→재빌드
python3 alio_sync.py --fresh  # 캐시 무시하고 처음부터
```

> 동기화에는 **Node.js**가 필요합니다(문서 추출에 `npx kordoc` 사용). 조회·검색 자체에는 불필요합니다. 전 규정 다운로드·추출로 수 분이 걸립니다.

### ③ 최신 받기 — `update`

Claude Desktop 채팅에서 "koica 도구 최신으로 업데이트해줘" → `update` 도구가 `git pull + 재빌드`를 자동 실행합니다. 데이터만 바뀐 경우 재시작 없이 즉시 반영, `.py`가 바뀐 경우 OS별 재시작 안내가 함께 출력됩니다.

**동기화 파이프라인** (`alio_sync.py`):
`ALIO 목록 조회 → 각 규정의 현행본(최신 개정본) 해석 → HWP 다운로드 → kordoc로 Markdown 추출 → Format A 정규화 → data/extracted/*.md + sources.json → 인덱스 재빌드`

---

## 데이터 구조

```
data/
├── extracted/                  # 규정별 .md 추출본 (유형_규정명.md)
│   ├── 규정_인사규정.md
│   ├── 지침_채용업무처리지침.md
│   ├── 시행세칙_인사규정 시행세칙.md
│   └── 정관_한국국제협력단 정관.md
├── sources.json                # 규정 매니페스트 (이름·유형·개정일·fileNo·origin)
├── index.json                  # 빌드 산출물 (gitignored)
└── _cache/                     # 동기화 캐시: HWP·원시 md (gitignored)
alio_sync.py                    # ALIO 동기화 파이프라인
koica_search.py                 # 인덱싱·검색 엔진 + CLI
koica_mcp_server.py             # MCP 서버 (10개 도구)
```

---

## 새 규정 추가·오류 신고

ALIO 규정 목록에 올라오는 규정은 자동 동기화로 반영되므로 별도 요청이 필요 없습니다. 다만 **추출 품질 오류**(조문 누락·표 깨짐 등)나 **ALIO에 없는 문서** 추가는 이슈로 알려주세요.

- [버그·정확도 신고](../../issues/new?template=bug-report.md)
- [규정 관련 요청](../../issues/new?template=regulation-update.md)

---

## 함께 쓰면 좋은 도구

KOICA 규정에 자주 등장하는 외부 법령(공공기관운영법, 국제개발협력기본법, 국가공무원법, 청탁금지법 등)을 더 깊이 다루려면 [`korean-law-mcp`](https://github.com/chrisryugj/korean-law-mcp)를 함께 사용하세요. 두 MCP를 동시에 활성화하면 Claude가 자연어 질의에서 양쪽을 자동 호출해, **한 번 질문에 KOICA 규정 + 한국 법령 통합 답변**을 받을 수 있습니다.

---

## 기술 메모

- **검색 엔진**: 순수 Python + 표준 라이브러리 (의존성: `mcp` 패키지 하나)
- **동기화**: `alio_sync.py` — ALIO REST(JSON) 조회 + [`kordoc`](https://www.npmjs.com/package/kordoc)(HWP→Markdown, Node) + Format A 정규화
- **macOS NFD 자모 분해** 자동 정규화
- **두 가지 추출 포맷 지원**: 마크다운 정형(`### 제N조`) 및 평문 PDF(`제N조(...) ...`)
- **검색 알고리즘**: 한국어 stopword 제거 + 토큰 IDF 가중 substring 매칭 + 음절 bi-gram fuzzy
- **별표·별지 분리 인덱싱**: 본문 조문 검색에서 서식·기준표 노이즈 제거
- **인덱스 캐싱**: MCP 서버 시작 시 전 조문을 메모리에 로드, 매 호출 시 재사용

---

## 로드맵

- [x] v1.0 — 조문 단위 검색, 본문 조회, 인용 검증, 상호참조, 시험문제 매핑
- [x] v1.2 — `update` 도구: 자연어로 git pull + build
- [x] v1.3 — 음절 bi-gram fuzzy 매칭
- [x] v1.4 — 별표·별지 분리 인덱싱 + `list_attachments`/`get_attachment`
- [x] v2.0 — **ALIO 자동 동기화**: 현행 규정 149개로 확장, `alio_sync.py` 파이프라인 + `sync_from_alio` 도구 + 주간 GitHub Actions. 규정 유형 기반 태깅으로 전환.

---

## 라이센스

MIT License. 자세한 내용은 [LICENSE](LICENSE).

KOICA 규정 원문의 저작권은 한국국제협력단에 있습니다. 본 repo의 추출본은 KOICA가 ALIO(공공기관 경영정보 공개시스템)를 통해 공개한 자료를 기반으로 정리한 것이며, 학습·업무 참조 목적의 fair use 범위 안에서 사용해 주세요.
