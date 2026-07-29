# KOICA 규정 MCP — 원격 HTTP 서버 이미지
FROM node:20-slim AS node_runtime

FROM python:3.12-slim

WORKDIR /app

# ALIO가 GitHub-hosted runner의 접속을 차단하므로 동기화는 Fly(도쿄)에서 실행한다.
# 런타임 이미지에 Node/kordoc를 포함해 HWP→Markdown 변환을 네트워크 설치 없이 수행.
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g kordoc@3.17.0 pdfjs-dist

# 의존성 먼저 설치 (레이어 캐시: 코드/데이터만 바뀌면 재설치 안 함)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 (server_http → koica_mcp_server → koica_search / usage_stats 의존)
# stats_cli.py: 소유자 전용 사용량 조회(공개 MCP엔 usage_stats 미노출, fly ssh로만 읽음)
COPY koica_search.py koica_mcp_server.py server_http.py usage_stats.py stats_cli.py alio_sync.py ./

# 데이터: 검색 인덱스 + 규정 본문. _cache(원본 HWP)/시험범위 PDF는 .dockerignore로 제외
COPY data/ ./data/

# index.json(검색 인덱스)은 빌드 산출물이라 git·저장소에 없다. 따라서 이미지
# 빌드 시 extracted → index.json 을 직접 생성한다. 이렇게 해야 로컬 `fly deploy`든
# GitHub Actions 자동배포(저장소 checkout)든 항상 데이터가 포함된다.
RUN python3 koica_search.py build

ENV PORT=8080
EXPOSE 8080

CMD ["python", "server_http.py"]
