# KOICA 규정 MCP — 원격 HTTP 서버 이미지
FROM node:24-slim AS node_runtime

# Lock the complete converter graph; npm is confined to this build stage.
WORKDIR /opt/koica-tools
COPY tools/document-converter/package*.json ./
# CPU bindings are bundled; the Fly service has no GPU and needs no CUDA download.
RUN ONNXRUNTIME_NODE_INSTALL=skip npm ci && npm audit

# 의미 검색 모델은 규정 데이터와 분리된 레이어에 내려받는다. 규정만 갱신된 배포에서는
# 약 124 MB 모델·토크나이저 레이어를 재사용하며, 다운로드 도구가 고정 리비전·SHA-256을 검증한다.
FROM python:3.12-slim AS semantic_model

WORKDIR /model-build

COPY semantic_search.py ./
RUN python3 semantic_search.py download-model --model-dir /opt/koica-semantic-model

FROM scratch AS index_artifacts
COPY docker-index/ /

FROM python:3.12-slim

WORKDIR /app

# ALIO가 GitHub-hosted runner의 접속을 차단하므로 동기화는 Fly(도쿄)에서 실행한다.
# 런타임 이미지에 Node/kordoc를 포함해 HWP→Markdown 변환을 네트워크 설치 없이 수행.
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /opt/koica-tools /opt/koica-tools
RUN ln -s /opt/koica-tools/node_modules/kordoc/dist/cli.js /usr/local/bin/kordoc

# 의존성 먼저 설치 (레이어 캐시: 코드/데이터만 바뀌면 재설치 안 함)
COPY requirements.txt requirements.lock.txt .
RUN python -m pip install --no-cache-dir --upgrade pip==26.2 \
 && python -m pip install --no-cache-dir --no-deps -r requirements.lock.txt \
 && python -m pip check

# 애플리케이션 코드 (server_http → koica_mcp_server → koica_search / usage_stats 의존)
# stats_cli.py: 소유자 전용 사용량 조회(공개 MCP엔 usage_stats 미노출, fly ssh로만 읽음)
COPY koica_search.py semantic_search.py index_artifact.py koica_mcp_server.py server_http.py usage_stats.py usage_metrics.py stats_cli.py alio_sync.py ./
COPY tests/ ./tests/
COPY LICENSE NOTICE /usr/share/doc/koica-reg-mcp/

# 공개 서버는 외부 임베딩 API를 호출하지 않는다. 빌드 단계에서 검증한 고정 E5 모델을
# 복사해 런타임 추론을 완전히 로컬·오프라인으로 수행한다.
COPY --from=semantic_model /opt/koica-semantic-model /opt/koica-semantic-model
ENV KOICA_SEMANTIC_MODEL_DIR=/opt/koica-semantic-model

# 데이터: 검색 인덱스 + 규정 본문. _cache(원본 HWP)/시험범위 PDF는 .dockerignore로 제외
COPY data/ ./data/

# CI/Fly는 prebuilt를 명시해 누락·오래된 artifact를 조용히 재계산하지 않는다.
# 로컬 기본값 auto는 artifact가 없으면 기존처럼 완전한 인덱스를 생성한다.
# bind mount로 전달해 최종 이미지에 artifact 사본을 중복 저장하지 않는다.
ARG KOICA_INDEX_SOURCE=auto
ARG KOICA_INDEX_REVISION=""
RUN --mount=type=bind,from=index_artifacts,target=/opt/index-artifact \
    python3 -c "from mcp.server.fastmcp import FastMCP" \
 && case "$KOICA_INDEX_SOURCE" in \
      prebuilt) python3 index_artifact.py install --directory /opt/index-artifact --revision "$KOICA_INDEX_REVISION" ;; \
      auto) if [ -f /opt/index-artifact/manifest.json ]; then \
              python3 index_artifact.py install --directory /opt/index-artifact --revision "$KOICA_INDEX_REVISION"; \
            else \
              KOICA_SEMANTIC_THREADS=4 python3 koica_search.py build --strict-semantic \
              && python3 tests/evaluate_semantic_quality.py; \
            fi ;; \
      *) echo "Unknown KOICA_INDEX_SOURCE: $KOICA_INDEX_SOURCE" >&2; exit 1 ;; \
    esac

# 이미지 빌드에는 네 코어를 쓰되, 512MB 운영 머신에서는 ONNX 단일
# 추론이 하나의 CPU 스레드만 쓰게 한다. 동시 세션 실행은 DenseEncoder 락이 직렬화한다.
ENV KOICA_SEMANTIC_THREADS=1
ENV PORT=8080
EXPOSE 8080 9091

CMD ["python", "server_http.py"]
