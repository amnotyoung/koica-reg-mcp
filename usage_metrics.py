"""Fly Prometheus scraper용 KOICA MCP 사용량 메트릭.

공개 MCP 포트(8080)와 분리된 내부 포트에서만 제공한다. 원본은 Fly 볼륨의
``usage.db``이며, 검색어·IP·세션 같은 개인정보는 포함하지 않는다.
"""

from __future__ import annotations

import datetime
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import usage_stats


METRICS_PATH = "/metrics"


def _label(value: object) -> str:
    """Prometheus text exposition label 값을 이스케이프한다."""
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _week_start(day: str) -> str:
    date = datetime.date.fromisoformat(day)
    return (date - datetime.timedelta(days=date.weekday())).isoformat()


def _weekly_buckets(daily: list[dict]) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    totals: dict[str, int] = {}
    tools: dict[tuple[str, str], int] = {}
    for row in daily:
        week = _week_start(row["day"])
        totals[week] = totals.get(week, 0) + int(row.get("total", 0))
        for tool, count in row.get("tools", {}).items():
            key = (week, str(tool))
            tools[key] = tools.get(key, 0) + int(count)
    return totals, tools


def render_metrics(snap: dict | None = None) -> str:
    """현재 SQLite 스냅샷을 Prometheus text exposition 형식으로 직렬화한다."""
    if snap is None:
        snap = usage_stats.snapshot()

    enabled = bool(snap.get("enabled"))
    read_error = bool(snap.get("error"))
    tools = snap.get("tools", [])
    daily = snap.get("daily", [])
    total = sum(int(row.get("count", 0)) for row in tools)
    weekly_totals, weekly_tools = _weekly_buckets(daily)

    lines = [
        "# HELP koica_reg_mcp_stats_enabled Whether persistent usage statistics are enabled.",
        "# TYPE koica_reg_mcp_stats_enabled gauge",
        f"koica_reg_mcp_stats_enabled {1 if enabled else 0}",
        "# HELP koica_reg_mcp_stats_read_error Whether the latest SQLite statistics read failed.",
        "# TYPE koica_reg_mcp_stats_read_error gauge",
        f"koica_reg_mcp_stats_read_error {1 if read_error else 0}",
        "# HELP koica_reg_mcp_calls_total Total MCP tool invocations recorded by the server.",
        "# TYPE koica_reg_mcp_calls_total counter",
        f"koica_reg_mcp_calls_total {total}",
        "# HELP koica_reg_mcp_tool_calls_total MCP tool invocations by tool.",
        "# TYPE koica_reg_mcp_tool_calls_total counter",
    ]

    for row in sorted(tools, key=lambda item: str(item.get("tool", ""))):
        tool = _label(row.get("tool", "unknown"))
        lines.append(f'koica_reg_mcp_tool_calls_total{{tool="{tool}"}} {int(row.get("count", 0))}')

    lines.extend([
        "# HELP koica_reg_mcp_weekly_calls MCP tool invocations grouped into KST Monday-start weeks.",
        "# TYPE koica_reg_mcp_weekly_calls gauge",
    ])
    for week, count in sorted(weekly_totals.items()):
        lines.append(f'koica_reg_mcp_weekly_calls{{week_start="{_label(week)}"}} {count}')

    lines.extend([
        "# HELP koica_reg_mcp_weekly_tool_calls MCP tool invocations by KST week and tool.",
        "# TYPE koica_reg_mcp_weekly_tool_calls gauge",
    ])
    for (week, tool), count in sorted(weekly_tools.items()):
        lines.append(
            "koica_reg_mcp_weekly_tool_calls"
            f'{{week_start="{_label(week)}",tool="{_label(tool)}"}} {count}'
        )

    lines.append("# EOF")
    return "\n".join(lines) + "\n"


class _MetricsHandler(BaseHTTPRequestHandler):
    server_version = "koica-reg-metrics/1"

    def _write_response(self, include_body: bool) -> None:
        if urlsplit(self.path).path != METRICS_PATH:
            body = b"not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        else:
            body = render_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._write_response(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._write_response(include_body=False)

    def log_message(self, _format: str, *_args: object) -> None:
        # 15초마다 들어오는 Fly scrape가 애플리케이션 로그를 채우지 않게 한다.
        return


def start_metrics_server(host: str = "0.0.0.0", port: int = 9091) -> ThreadingHTTPServer:
    """백그라운드 daemon thread에서 내부 Prometheus endpoint를 시작한다."""
    server = ThreadingHTTPServer((host, port), _MetricsHandler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="koica-prometheus-metrics",
        daemon=True,
    )
    thread.start()
    return server
