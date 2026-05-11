---
name: 버그·정확도 신고
about: 추출 오류, 검색 결과 부정확, 도구 동작 이상 등
title: "[버그] "
labels: bug
---

## 문제 유형
- [ ] 본문 추출 오류 (조문 누락·잘못된 합침·깨진 문자 등)
- [ ] 검색 결과가 부정확 (원하는 조문이 안 나옴)
- [ ] `verify_citation` 오탐·미탐
- [ ] `find_references` 누락
- [ ] MCP 서버 시작 실패
- [ ] 기타

## 재현 방법

어떤 도구를 어떤 입력으로 호출했는지 적어주세요.

예시:
```
search_regulation(query="해외사무소장 정산", category="finance")
```

## 기대 결과 vs 실제 결과

**기대:** 해외사무소 운영 예산 집행 및 회계처리 지침 제11조가 상위에 나와야 함
**실제:** ...

## 환경

- OS: (macOS / Windows / Linux)
- 클라이언트: (Claude Desktop / Claude Code / Cursor / 기타)
- Python 버전:
