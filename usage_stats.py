"""도구 호출 횟수 집계 — 개인정보 없이 카운트만 영속 저장.

무엇을 저장하나:
  - 도구명(예: "search_regulation")과 누적 호출 횟수, 최초/최근 호출 시각(UTC).
  - 자연어 검색어의 '언어 라벨'(ko/en/other)별 누적 카운트. 검색어 원문이 아니라
    어떤 문자체계인지(한글 포함→ko / 라틴문자 포함→en / 그 외→other)만 판별해
    라벨만 센다.
  - 위 둘의 '일자별' 버킷(KST 날짜 기준): 날짜+도구+횟수, 날짜+언어+횟수.
    "이번 주/일자별"처럼 기간을 잘라 보기 위한 것.
무엇을 저장하지 '않'나:
  - 검색어·인자 원문, 응답 내용, 클라이언트 IP, 그 어떤 신원 정보도 저장하지 않는다.
  언어 라벨·날짜·횟수는 개인을 식별하지 않으므로, 전체적으로 개인정보(개인정보보호법상
  IP 포함) 수집에 해당하지 않는다.

일자 기준(KST):
  일자 버킷의 '날짜'는 한국 시간(UTC+9, DST 없음) 기준이다. 최초/최근 시각
  타임스탬프는 UTC로 저장하지만, "며칠에 몇 건"의 날짜 경계는 한국 사용자의
  하루(자정~자정 KST)와 맞춘다.

언어 신호의 한계:
  코퍼스가 한국어라 LLM이 영어 질문을 한국어 검색어로 번역해 보내는 경우가 많다.
  'en'이 잡히면 외국어 사용이 확실히 있다는 뜻이지만, 'ko'뿐이라고 외국인이
  없다는 보장은 안 된다(단방향 신호).

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

# 한국 표준시(UTC+9, 서머타임 없음) — 일자 버킷의 날짜 경계 기준.
_KST = datetime.timezone(datetime.timedelta(hours=9))


def _db_path() -> str | None:
    """집계 DB 경로. 미설정이면 None → 집계 비활성."""
    path = os.environ.get("KOICA_STATS_DB")
    return path or None


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _kst_day() -> str:
    """오늘 날짜(KST) 'YYYY-MM-DD'. 일자 버킷 키."""
    return datetime.datetime.now(_KST).date().isoformat()


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
    # 누적(전체 기간)
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
    # 일자별 버킷(KST 날짜)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_tool ("
        " day TEXT, tool TEXT,"
        " count INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (day, tool))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_lang ("
        " day TEXT, lang TEXT,"
        " count INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (day, lang))"
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


def _bump_daily(conn: sqlite3.Connection, table: str, key_col: str, day: str, key: str) -> None:
    conn.execute(
        f"INSERT INTO {table}(day, {key_col}, count) VALUES(?, ?, 1)"
        f" ON CONFLICT(day, {key_col}) DO UPDATE SET count = count + 1",
        (day, key),
    )


def record(tool: str, text: str | None = None) -> None:
    """도구 1회 호출을 기록(누적 +1, 오늘(KST) 일자 버킷 +1).

    text가 주어지면(자연어 검색어) 그 언어 라벨(ko/en/other)도 누적·일자로 집계.
    검색어 원문은 저장하지 않고 라벨만 센다. 실패는 조용히 무시한다(best-effort).
    """
    path = _db_path()
    if not path:
        return
    try:
        now = _now()
        day = _kst_day()
        lang = detect_lang(text)
        with _LOCK:
            conn = _connect(path)
            try:
                _bump(conn, "tool_usage", "tool", tool, now)
                _bump_daily(conn, "daily_tool", "tool", day, tool)
                if lang:
                    _bump(conn, "lang_usage", "lang", lang, now)
                    _bump_daily(conn, "daily_lang", "lang", day, lang)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        # 집계는 부가 기능 — 어떤 실패도 도구 동작을 막지 않는다.
        pass


def _daily_breakdown(conn: sqlite3.Connection) -> list[dict]:
    """일자별 버킷을 {day, total, tools:{}, by_language:{}} 리스트(최근 날짜 순)로."""
    days: dict[str, dict] = {}

    def _slot(day: str) -> dict:
        return days.setdefault(day, {"day": day, "total": 0, "tools": {}, "by_language": {}})

    for day, tool, count in conn.execute(
        "SELECT day, tool, count FROM daily_tool"
    ).fetchall():
        slot = _slot(day)
        slot["tools"][tool] = count
        slot["total"] += count            # total 은 도구 호출 합계
    for day, lang, count in conn.execute(
        "SELECT day, lang, count FROM daily_lang"
    ).fetchall():
        _slot(day)["by_language"][lang] = count   # 언어는 하위 분해라 total 에 더하지 않음

    return sorted(days.values(), key=lambda d: d["day"], reverse=True)


def snapshot() -> dict:
    """현재 집계 스냅샷.

    Returns:
        {
          "enabled": bool,           # KOICA_STATS_DB 설정 여부(영속화 활성)
          "total": int,             # 전체 도구 누적 호출 합
          "tools": [{"tool","count","first_seen","last_seen"}, …],       # 많은 순
          "by_language": [{"lang","count","first_seen","last_seen"}, …], # 많은 순
          "daily": [                # 일자별(KST) 버킷, 최근 날짜 순
            {"day","total","tools":{tool:count},"by_language":{lang:count}}, …
          ],
        }
        영속화 비활성 시 enabled=False, 나머지는 빈 값.
    """
    path = _db_path()
    if not path:
        return {"enabled": False, "total": 0, "tools": [], "by_language": [], "daily": []}
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
                daily = _daily_breakdown(conn)
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
            "daily": daily,
        }
    except Exception as exc:
        # 읽기 실패도 조회 자체를 깨지 않도록 형태를 유지해 반환.
        return {"enabled": True, "total": 0, "tools": [], "by_language": [], "daily": [], "error": str(exc)}
