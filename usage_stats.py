"""도구 호출 횟수 집계 — 개인정보 없이 카운트만 영속 저장.

무엇을 저장하나:
  - 도구명(예: "search_regulation")과 누적 호출 횟수, 최초/최근 호출 시각(UTC).
  - 자연어 검색어의 '언어 라벨'(ko/en/other)별 카운트. 검색어 원문이 아니라
    어떤 문자체계인지(한글 포함→ko / 라틴문자 포함→en / 그 외→other)만 판별해
    라벨만 센다. "국내만 쓰나 해외도 쓰나"를 가늠하기 위한 거친 신호다.
무엇을 저장하지 '않'나:
  - 검색어·인자 원문, 응답 내용, 클라이언트 IP, 그 어떤 신원 정보도 저장하지 않는다.
  언어 라벨은 개인을 식별하지 않으므로, 전체적으로 개인정보(개인정보보호법상 IP
  포함) 수집에 해당하지 않는다.

주의(언어 신호의 한계):
  코퍼스가 한국어라 LLM이 영어 사용자의 질문도 한국어 검색어로 번역해 보내는
  경우가 많다. 따라서 'en'이 잡히면 외국어 사용이 확실히 있다는 뜻이지만,
  'ko'뿐이라고 해서 외국인이 없다는 보장은 안 된다(단방향 신호).

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


def detect_lang(text: str | None) -> str | None:
    """검색어의 언어 라벨을 반환한다 — 개인정보 아님(원문 미저장, 라벨만).

    - 한글(음절·자모)이 하나라도 있으면 "ko" (예: "승진 가점", "KOICA 승진")
    - 아니고 라틴문자가 있으면 "en" (영어 등 라틴 문자체계, 예: "promotion bonus")
    - 그 외(숫자·기호·다른 문자체계만)면 "other"
    - 비었거나 공백뿐이면 None(집계 제외)

    한글 우선 판정이라 "KOICA 승진"처럼 한글+영문 혼용은 ko로 잡힌다(한국인이
    영문 약어를 섞어 쓴 경우를 올바르게 분류).
    """
    if not text or not text.strip():
        return None
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            return "ko"
    for ch in text:
        o = ord(ch)
        if 0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A:
            return "en"
    return "other"


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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lang_usage ("
        " lang TEXT PRIMARY KEY,"
        " count INTEGER NOT NULL DEFAULT 0,"
        " first_seen TEXT,"
        " last_seen TEXT)"
    )
    return conn


def _bump(conn: sqlite3.Connection, table: str, key_col: str, key: str, now: str) -> None:
    conn.execute(
        f"INSERT INTO {table}({key_col}, count, first_seen, last_seen)"
        " VALUES(?, 1, ?, ?)"
        f" ON CONFLICT({key_col}) DO UPDATE SET"
        "  count = count + 1,"
        "  last_seen = excluded.last_seen",
        (key, now, now),
    )


def record(tool: str, text: str | None = None) -> None:
    """도구 1회 호출을 기록(누적 +1).

    text가 주어지면(자연어 검색어) 그 언어 라벨(ko/en/other)도 함께 집계한다.
    검색어 원문은 저장하지 않고 라벨만 센다. 실패는 조용히 무시한다(best-effort).
    """
    path = _db_path()
    if not path:
        return
    try:
        now = _now()
        lang = detect_lang(text)
        with _LOCK:
            conn = _connect(path)
            try:
                _bump(conn, "tool_usage", "tool", tool, now)
                if lang:
                    _bump(conn, "lang_usage", "lang", lang, now)
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
          "by_language": [          # 자연어 검색어 언어 라벨별 집계(많은 순)
            {"lang", "count", "first_seen", "last_seen"}, …
          ],
        }
        영속화 비활성 시 enabled=False, 나머지는 빈 값.
    """
    path = _db_path()
    if not path:
        return {"enabled": False, "total": 0, "tools": [], "by_language": []}
    try:
        with _LOCK:
            conn = _connect(path)
            try:
                tool_rows = conn.execute(
                    "SELECT tool, count, first_seen, last_seen"
                    " FROM tool_usage ORDER BY count DESC, tool ASC"
                ).fetchall()
                lang_rows = conn.execute(
                    "SELECT lang, count, first_seen, last_seen"
                    " FROM lang_usage ORDER BY count DESC, lang ASC"
                ).fetchall()
            finally:
                conn.close()
        tools = [
            {"tool": r[0], "count": r[1], "first_seen": r[2], "last_seen": r[3]}
            for r in tool_rows
        ]
        by_language = [
            {"lang": r[0], "count": r[1], "first_seen": r[2], "last_seen": r[3]}
            for r in lang_rows
        ]
        return {
            "enabled": True,
            "total": sum(t["count"] for t in tools),
            "tools": tools,
            "by_language": by_language,
        }
    except Exception as exc:
        # 읽기 실패도 도구 목록 조회 자체를 깨지 않도록 형태를 유지해 반환.
        return {"enabled": True, "total": 0, "tools": [], "by_language": [], "error": str(exc)}
