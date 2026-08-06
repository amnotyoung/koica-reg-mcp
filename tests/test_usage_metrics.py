import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

import usage_metrics


SNAPSHOT = {
    "enabled": True,
    "total": 11,
    "tools": [
        {"tool": "search_regulation", "count": 7},
        {"tool": 'quote"tool', "count": 4},
    ],
    "daily": [
        {
            "day": "2026-08-02",  # Sunday: week starting 2026-07-27
            "total": 5,
            "tools": {"search_regulation": 3, 'quote"tool': 2},
            "by_language": {},
        },
        {
            "day": "2026-08-03",  # Monday: new week
            "total": 6,
            "tools": {"search_regulation": 4, 'quote"tool': 2},
            "by_language": {},
        },
    ],
}


class RenderMetricsTests(unittest.TestCase):
    def test_renders_cumulative_and_kst_monday_week_buckets(self):
        output = usage_metrics.render_metrics(SNAPSHOT)

        self.assertIn("koica_reg_mcp_calls_total 11", output)
        self.assertIn(
            'koica_reg_mcp_tool_calls_total{tool="search_regulation"} 7',
            output,
        )
        self.assertIn(
            'koica_reg_mcp_tool_calls_total{tool="quote\\\"tool"} 4',
            output,
        )
        self.assertIn(
            'koica_reg_mcp_weekly_calls{week_start="2026-07-27"} 5',
            output,
        )
        self.assertIn(
            'koica_reg_mcp_weekly_calls{week_start="2026-08-03"} 6',
            output,
        )
        self.assertTrue(output.endswith("# EOF\n"))

    def test_reports_disabled_or_failed_stats_without_raising(self):
        output = usage_metrics.render_metrics({
            "enabled": False,
            "error": "unavailable",
            "tools": [],
            "daily": [],
        })

        self.assertIn("koica_reg_mcp_stats_enabled 0", output)
        self.assertIn("koica_reg_mcp_stats_read_error 1", output)
        self.assertIn("koica_reg_mcp_calls_total 0", output)


class MetricsHttpServerTests(unittest.TestCase):
    def setUp(self):
        render_patch = patch.object(
            usage_metrics,
            "render_metrics",
            return_value="test_metric 1\n",
        )
        render_patch.start()
        self.addCleanup(render_patch.stop)
        try:
            self.server = usage_metrics.start_metrics_server(host="127.0.0.1", port=0)
        except PermissionError:
            self.skipTest("test sandbox does not allow local socket binding")
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_metrics_endpoint_is_scrapeable(self):
        with urllib.request.urlopen(f"{self.base_url}/metrics", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"test_metric 1\n")
            self.assertIn("version=0.0.4", response.headers["Content-Type"])

    def test_other_paths_are_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base_url}/", timeout=2)
        self.assertEqual(raised.exception.code, 404)
