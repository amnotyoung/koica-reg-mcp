"""소유자 전용 사용량 통계 조회 CLI.

공개 MCP 표면에서 usage_stats 도구를 제거했으므로(누구나 조회 방지), 프로덕션
통계는 이 스크립트를 Fly 머신 안에서 실행해 owner 인증(flyctl)으로만 조회한다.

사용법(Fly 머신 안, fly ssh 로만 접근):
    fly ssh console --app koica-reg-mcp -C "python3 /app/stats_cli.py"              # 전체(누적+일자별)
    fly ssh console --app koica-reg-mcp -C "python3 /app/stats_cli.py week"         # 이번 주(KST, 월~일)
    fly ssh console --app koica-reg-mcp -C "python3 /app/stats_cli.py backfill-legacy"  # 1회성 과거분 일자 반영

- 집계(record) 자체는 공개 서버에서 계속 이뤄지고, '읽기'만 소유자로 제한된다.
- KOICA_STATS_DB 미설정 시 Fly 볼륨 경로(/data/usage.db)를 기본값으로 쓴다.
  로컬에서 다른 DB를 보려면 KOICA_STATS_DB 를 지정한 뒤 실행하면 된다.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

# fly ssh 세션은 앱 프로세스의 [env]를 물려받지 않을 수 있으므로 기본 경로를 보장.
os.environ.setdefault("KOICA_STATS_DB", "/data/usage.db")

import usage_stats

_KST = datetime.timezone(datetime.timedelta(hours=9))


def _this_week_range() -> tuple[str, str]:
    """이번 주(KST) 월요일~일요일 날짜 범위 'YYYY-MM-DD'."""
    today = datetime.datetime.now(_KST).date()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _sorted_desc(counts: dict) -> dict:
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _week_view(snap: dict) -> dict:
    start, end = _this_week_range()
    days = [d for d in snap.get("daily", []) if start <= d["day"] <= end]
    tools: dict = {}
    langs: dict = {}
    total = 0
    for d in days:
        total += d["total"]
        for t, c in d["tools"].items():
            tools[t] = tools.get(t, 0) + c
        for lang, c in d["by_language"].items():
            langs[lang] = langs.get(lang, 0) + c
    return {
        "mode": "week",
        "tz": "KST",
        "week_start": start,
        "week_end": end,
        "total": total,
        "tools": _sorted_desc(tools),
        "by_language": _sorted_desc(langs),
        "days": days,
    }


def _backfill_legacy() -> dict:
    """일자 버킷 도입 이전의 누적분을 일자 버킷에 1회 반영.

    각 도구/언어의 누적 카운트를 그 last_seen 날짜(KST)에 귀속시킨다. INSERT OR
    IGNORE 라 같은 (날짜, 키)가 이미 있으면 건드리지 않으므로 여러 번 실행해도
    안전하다. 이후 트래픽은 record()가 실시간으로 일자 버킷에 쌓는다.

    한계: 한 도구의 과거 호출이 여러 날에 걸쳐 있었다면 전부 last_seen 날짜 하루로
    몰아 귀속된다(근사). 도입 전환 시점의 1회성 시드 용도다.
    """
    path = usage_stats._db_path()
    if not path:
        return {"ok": False, "reason": "KOICA_STATS_DB 미설정"}
    conn = usage_stats._connect(path)  # 테이블 보장
    try:
        c1 = conn.execute(
            "INSERT OR IGNORE INTO daily_tool(day, tool, count)"
            " SELECT substr(datetime(last_seen, '+9 hours'), 1, 10), tool, count"
            " FROM tool_usage"
        )
        inserted_tool = c1.rowcount
        c2 = conn.execute(
            "INSERT OR IGNORE INTO daily_lang(day, lang, count)"
            " SELECT substr(datetime(last_seen, '+9 hours'), 1, 10), lang, count"
            " FROM lang_usage"
        )
        inserted_lang = c2.rowcount
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "inserted_daily_tool_rows": inserted_tool,
        "inserted_daily_lang_rows": inserted_lang,
    }


def main(argv: list[str]) -> None:
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "backfill-legacy":
        result = _backfill_legacy()
        result["result_week"] = _week_view(usage_stats.snapshot())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    snap = usage_stats.snapshot()
    out = _week_view(snap) if mode == "week" else snap
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv)
