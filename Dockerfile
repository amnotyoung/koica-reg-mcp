# KOICA 규정 MCP — 원격 HTTP 서버 이미지
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 (레이어 캐시: 코드/데이터만 바뀌면 재설치 안 함)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 (server_http → koica_mcp_server → koica_search 의존)
COPY koica_search.py koica_mcp_server.py server_http.py ./

# 데이터: 검색 인덱스 + 규정 본문. _cache(원본 HWP)/시험범위 PDF는 .dockerignore로 제외
COPY data/ ./data/

# index.json(검색 인덱스)은 빌드 산출물이라 git·저장소에 없다. 따라서 이미지
# 빌드 시 extracted → index.json 을 직접 생성한다. 이렇게 해야 로컬 `fly deploy`든
# GitHub Actions 자동배포(저장소 checkout)든 항상 데이터가 포함된다.
RUN python3 koica_search.py build

ENV PORT=8080
EXPOSE 8080

CMD ["python", "server_http.py"]
