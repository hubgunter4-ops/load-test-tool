import unittest
from unittest.mock import patch

from loadtest.external_tools import (
    ExternalConfig,
    ExternalToolError,
    build_command,
    build_pktgen_script,
    plan,
)


class ExternalToolsTests(unittest.TestCase):
    def test_wrk_command_uses_concurrent_connections(self):
        config = ExternalConfig("wrk", "https://example.test/health", connections=40, duration=20, threads=4)
        with patch("loadtest.external_tools.tool_path", return_value="/usr/local/bin/wrk"):
            command = build_command(config)
        self.assertEqual(command[:5], ["/usr/local/bin/wrk", "-t4", "-c40", "-d20s", "--latency"])
        self.assertEqual(command[-1], "https://example.test/health")

    def test_wrk2_command_adds_constant_rate(self):
        config = ExternalConfig("wrk2", "https://example.test", rate=250)
        with patch("loadtest.external_tools.tool_path", return_value="/usr/local/bin/wrk2"):
            command = build_command(config)
        self.assertIn("-R250", command)

    def test_slowhttptest_is_bounded(self):
        config = ExternalConfig("slowhttptest", "https://example.test", connections=50, duration=30)
        with patch("loadtest.external_tools.tool_path", return_value="/usr/bin/slowhttptest"):
            command = build_command(config)
        self.assertIn("-H", command)
        self.assertIn("-c", command)
        self.assertIn("50", command)

    def test_iperf3_supports_parallel_udp_streams(self):
        config = ExternalConfig("iperf3", "127.0.0.1", connections=3, protocol="udp", rate=5000)
        with patch("loadtest.external_tools.tool_path", return_value="/usr/bin/iperf3"):
            command = build_command(config)
        self.assertEqual(command[:7], ["/usr/bin/iperf3", "-c", "127.0.0.1", "-t", "30", "-P", "3"])
        self.assertIn("-u", command)
        self.assertIn("5000K", command)

    def test_pktgen_generates_script_without_sending(self):
        config = ExternalConfig("pktgen", "198.18.0.2", interface="lo", destination="127.0.0.1", count=100)
        script = build_pktgen_script(config)
        self.assertIn("add_device lo", script)
        self.assertIn("count 100", script)
        self.assertIn("ratep 100", script)
        self.assertIn("dst_min 127.0.0.1", script)

    def test_plan_is_json_serializable_and_does_not_execute(self):
        config = ExternalConfig("wrk", "https://example.test")
        with patch("loadtest.external_tools.tool_path", return_value="/usr/local/bin/wrk"):
            result = plan(config)
        self.assertEqual(result["command"][-1], "https://example.test")
        self.assertFalse(result.get("executed", False))

    def test_limits_reject_unbounded_values(self):
        config = ExternalConfig("wrk", "https://example.test", connections=1001)
        with patch("loadtest.external_tools.tool_path", return_value="/usr/local/bin/wrk"):
            with self.assertRaises(ExternalToolError):
                build_command(config)


if __name__ == "__main__":
    unittest.main()
