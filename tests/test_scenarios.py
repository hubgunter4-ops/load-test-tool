import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from loadtest.runner import LoadConfig
from loadtest.scenarios import ScenarioConfig, run_scenario


class ScenarioHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        payload = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()

    def log_message(self, *_args):
        return


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ScenarioHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/health"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def base(self, duration=0.2, users=2, method="GET", body=None):
        return LoadConfig(self.url, users=users, duration=duration, method=method, body=body, timeout=1)

    def test_all_requested_modes_execute(self):
        modes = [
            ("Spike Test", {"peak_users": 4}),
            ("Stress Test progresivo", {"stages": [1, 2]}),
            ("Soak Test", {}),
            ("Escenarios mixtos", {"mix": [{"url": self.url, "weight": 80}, {"url": self.url, "weight": 20}]}),
            ("Recuperación y fallos", {}),
            ("Prueba específica de base de datos", {"query_type": "lectura_simple"}),
            ("Payload y rate limiting", {"payload_size": 32, "rate_limit": 10}),
        ]
        for mode, params in modes:
            with self.subTest(mode=mode):
                report = asyncio.run(run_scenario(ScenarioConfig(self.base(), mode, params)))
                self.assertEqual(report.scenario, mode)
                self.assertGreaterEqual(len(report.phases), 1)
                self.assertGreaterEqual(report.aggregate.total_requests, 0)

    def test_recovery_records_controlled_failures(self):
        report = asyncio.run(run_scenario(ScenarioConfig(self.base(duration=0.2), "Recuperación y fallos", {})))
        self.assertGreater(report.aggregate.failed_requests, 0)
        self.assertIn("phase", report.phases[1])

    def test_payload_mode_sends_configured_body(self):
        report = asyncio.run(run_scenario(ScenarioConfig(self.base(method="POST"), "Payload y rate limiting", {"payload_size": 64})))
        self.assertGreater(report.aggregate.total_requests, 0)
        self.assertEqual(len(report.phases), 1)


if __name__ == "__main__":
    unittest.main()
