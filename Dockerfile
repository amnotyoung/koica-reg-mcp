# KOICA 규정 MCP — 원격 HTTP 서버 이미지
FROM node:20-slim AS node_runtime

# 의미 검색 모델은 규정 데이터와 분리된 레이어에 내려받는다. 규정만 갱신된 배포에서는
# 약 124 MB 모델·토크나이저 레이어를 재사용하며, 다운로드 도구가 고정 리비전·SHA-256을 검증한다.
FROM python:3.12-slim AS semantic_model

WORKDIR /model-build

COPY semantic_search.py ./
RUN python3 semantic_search.py download-model --model-dir /opt/koica-semantic-model

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
COPY koica_search.py semantic_search.py koica_mcp_server.py server_http.py usage_stats.py stats_cli.py alio_sync.py ./
COPY tests/evaluate_semantic_quality.py ./tests/
COPY LICENSE NOTICE /usr/share/doc/koica-reg-mcp/

# 공개 서버는 외부 임베딩 API를 호출하지 않는다. 빌드 단계에서 검증한 고정 E5 모델을
# 복사해 런타임 추론을 완전히 로컬·오프라인으로 수행한다.
COPY --from=semantic_model /opt/koica-semantic-model /opt/koica-semantic-model
ENV KOICA_SEMANTIC_MODEL_DIR=/opt/koica-semantic-model

# 데이터: 검색 인덱스 + 규정 본문. _cache(원본 HWP)/시험범위 PDF는 .dockerignore로 제외
COPY data/ ./data/

# 검색 인덱스는 빌드 산출물이라 git·저장소에 없다. 이미지 빌드 시 키워드 인덱스와
# 의미 벡터를 함께 생성하고, 의미 인덱스가 없거나 모델과 맞지 않으면 빌드를 실패시킨다.
# 이렇게 해야 로컬 `fly deploy`든 자동배포든 항상 실제 하이브리드 검색이 활성화된다.
RUN python3 -c "from mcp.server.fastmcp import FastMCP" \
 && KOICA_SEMANTIC_THREADS=4 python3 koica_search.py build --strict-semantic \
 && python3 tests/evaluate_semantic_quality.py

# 이미지 빌드에는 네 코어를 쓰되, 512MB 운영 머신에서는 ONNX 단일
# 추론이 하나의 CPU 스레드만 쓰게 한다. 동시 세션 실행은 DenseEncoder 락이 직렬화한다.
ENV KOICA_SEMANTIC_THREADS=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "server_http.py"]
