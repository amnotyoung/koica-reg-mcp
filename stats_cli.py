"""소유자 전용 사용량 통계 조회 CLI.

공개 MCP 표면에서 usage_stats 도구를 제거했으므로(누구나 조회 방지), 프로덕션
통계는 이 스크립트를 Fly 머신 안에서 실행해 owner 인증(flyctl)으로만 조회한다:

    fly ssh console --app koica-reg-mcp -C "python3 /app/stats_cli.py"

- 집계(record) 자체는 공개 서버에서 계속 이뤄지고, '읽기'만 소유자로 제한된다.
- KOICA_STATS_DB 미설정 시 Fly 볼륨 경로(/data/usage.db)를 기본값으로 쓴다.
  로컬에서 다른 DB를 보려면 KOICA_STATS_DB 를 지정한 뒤 실행하면 된다.
"""

from __future__ import annotations

import json
import os

# fly ssh 세션은 앱 프로세스의 [env]를 물려받지 않을 수 있으므로 기본 경로를 보장.
os.environ.setdefault("KOICA_STATS_DB", "/data/usage.db")

import usage_stats


if __name__ == "__main__":
    print(json.dumps(usage_stats.snapshot(), ensure_ascii=False, indent=2))
