import unittest

from loadtest.gui_config import GuiValues, config_from_values, parse_headers_text, scenario_from_values


class GuiHelpersTests(unittest.TestCase):
    def test_parse_headers_text(self):
        self.assertEqual(
            parse_headers_text("Authorization: Bearer token\nContent-Type: application/json"),
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )

    def test_parse_headers_rejects_invalid_line(self):
        with self.assertRaisesRegex(ValueError, "línea 1"):
            parse_headers_text("Authorization")

    def test_config_from_values(self):
        config = config_from_values(
            GuiValues(
                url="http://localhost:8000/api",
                method="post",
                users="12",
                duration="20",
                ramp_up="5",
                timeout="3",
                headers="Content-Type: application/json",
                body='{"ok":true}',
                verify_tls=False,
                report_path="report.json",
                chart_dir="charts",
                chart_format="png",
                auto_charts=True,
            )
        )
        self.assertEqual(config.method, "POST")
        self.assertEqual(config.users, 12)
        self.assertEqual(config.body, b'{"ok":true}')
        self.assertFalse(config.verify_tls)

    def test_config_from_values_rejects_bad_numbers(self):
        values = GuiValues("http://localhost", "GET", "0", "20", "0", "10", "", "", True, "report.json", "charts", "png", True)
        with self.assertRaisesRegex(ValueError, "mayor que cero"):
            config_from_values(values)

    def test_scenario_from_values_parses_parameters(self):
        values = GuiValues("http://localhost", "GET", "2", "1", "0", "1", "", "", True, "report.json", "charts", "png", True, "Spike Test", '{"peak_users": 8}')
        scenario = scenario_from_values(values)
        self.assertEqual(scenario.mode, "Spike Test")
        self.assertEqual(scenario.params["peak_users"], 8)

    def test_special_scenario_fields_use_defaults_and_user_values(self):
        pps_defaults = scenario_from_values(GuiValues(
            "http://localhost", "GET", "2", "1", "0", "1", "", "", True,
            "report.json", "charts", "png", False, scenario="PPS Stress",
        ))
        self.assertEqual(
            {key: pps_defaults.params[key] for key in ("host", "port", "pps", "packet_size")},
            {"host": "127.0.0.1", "port": 9000, "pps": 100, "packet_size": 512},
        )

        values = GuiValues(
            "http://localhost", "GET", "2", "1", "0", "1", "", "", True,
            "report.json", "charts", "png", False,
            scenario="PPS Stress", pps_host="stress.example.test", pps_port="1234",
            pps_rate="250", pps_packet_size="1024",
        )
        scenario = scenario_from_values(values)
        self.assertEqual(
            {key: scenario.params[key] for key in ("host", "port", "pps", "packet_size")},
            {"host": "stress.example.test", "port": 1234, "pps": 250, "packet_size": 1024},
        )

        throughput = scenario_from_values(GuiValues(
            "http://localhost", "GET", "2", "1", "0", "1", "", "", True,
            "report.json", "charts", "png", False, scenario="Throughput Stress",
        ))
        self.assertEqual(throughput.params["method"], "POST")
        self.assertEqual(throughput.params["payload_size"], 262144)

    def test_special_scenario_fields_enforce_limits(self):
        values = GuiValues(
            "http://localhost", "GET", "2", "1", "0", "1", "", "", True,
            "report.json", "charts", "png", False, scenario="PPS Stress", pps_rate="1001",
        )
        with self.assertRaisesRegex(ValueError, "entre 1 y 1000"):
            scenario_from_values(values)

        throughput = GuiValues(
            "http://localhost", "GET", "2", "1", "0", "1", "", "", True,
            "report.json", "charts", "png", False, scenario="Throughput Stress",
            throughput_payload_size="16777217",
        )
        with self.assertRaisesRegex(ValueError, "16777216"):
            scenario_from_values(throughput)


if __name__ == "__main__":
    unittest.main()
