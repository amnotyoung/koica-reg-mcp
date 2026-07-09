"""도구 호출 횟수 집계 — 개인정보 없이 도구명별 카운트만 영속 저장.

무엇을 저장하나:
  - 도구명(예: "search_regulation")과 누적 호출 횟수, 최초/최근 호출 시각(UTC).
무엇을 저장하지 '않'나:
  - 검색어·인자·응답 내용, 클라이언트 IP, 그 어떤 신원 정보도 저장하지 않는다.
  따라서 개인정보(개인정보보호법상 IP 포함) 수집에 해당하지 않는다.

영속화:
  SQLite 파일(순수 표준 라이브러리). 경로는 환경변수 KOICA_STATS_DB로 지정한다.
  Fly.io 에서는 볼륨(/data)에 두어 머신 정지·재배포에도 카운트가 보존된다.
  KOICA_STATS_DB 가 없으면(예: 로컬 stdio 기본) 집계는 조용히 비활성(no-op)된다.

안정성:
  집계는 부가 기능이므로 항상 best-effort다. DB 쓰기/읽기가 실패해도 예외를
  삼켜 도구 본연의 동작(규정 검색·조회)을 절대 방해하지 않는다.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import threading

# 프로세스 내 쓰기 직렬화. 각 호출은 자기 스레드에서 커넥션을 새로 열고 닫으므로
# sqlite3 기본(check_same_thread=True)과도 호환된다.
_LOCK = threading.Lock()


def _db_path() -> str | None:
    """집계 DB 경로. 미설정이면 None → 집계 비활성."""
    path = os.environ.get("KOICA_STATS_DB")
    return path or None


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_usage ("
        " tool TEXT PRIMARY KEY,"
        " count INTEGER NOT NULL DEFAULT 0,"
        " first_seen TEXT,"
        " last_seen TEXT)"
    )
    return conn


def record(tool: str) -> None:
    """도구 1회 호출을 기록(누적 +1). 실패는 조용히 무시한다."""
    path = _db_path()
    if not path:
        return
    try:
        now = _now()
        with _LOCK:
            conn = _connect(path)
            try:
                conn.execute(
                    "INSERT INTO tool_usage(tool, count, first_seen, last_seen)"
                    " VALUES(?, 1, ?, ?)"
                    " ON CONFLICT(tool) DO UPDATE SET"
                    "  count = count + 1,"
                    "  last_seen = excluded.last_seen",
                    (tool, now, now),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        # 집계는 부가 기능 — 어떤 실패도 도구 동작을 막지 않는다.
        pass


def snapshot() -> dict:
    """현재 집계 스냅샷.

    Returns:
        {
          "enabled": bool,           # KOICA_STATS_DB 설정 여부(영속화 활성)
          "total": int,             # 전체 도구 누적 호출 합
          "tools": [                # 호출 많은 순
            {"tool", "count", "first_seen", "last_seen"}, …
          ],
        }
        영속화 비활성 시 enabled=False, total=0, tools=[].
    """
    path = _db_path()
    if not path:
        return {"enabled": False, "total": 0, "tools": []}
    try:
        with _LOCK:
            conn = _connect(path)
            try:
                rows = conn.execute(
                    "SELECT tool, count, first_seen, last_seen"
                    " FROM tool_usage ORDER BY count DESC, tool ASC"
                ).fetchall()
            finally:
                conn.close()
        tools = [
            {"tool": r[0], "count": r[1], "first_seen": r[2], "last_seen": r[3]}
            for r in rows
        ]
        return {
            "enabled": True,
            "total": sum(t["count"] for t in tools),
            "tools": tools,
        }
    except Exception as exc:
        # 읽기 실패도 도구 목록 조회 자체를 깨지 않도록 형태를 유지해 반환.
        return {"enabled": True, "total": 0, "tools": [], "error": str(exc)}
