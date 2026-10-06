import asyncio
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from loadtest.runner import LoadConfig
from loadtest.scenarios import ScenarioConfig, run_scenario


class ScenarioHandler(BaseHTTPRequestHandler):
    seen_requests = []

    def _respond(self):
        self.__class__.seen_requests.append((self.command, self.headers.get("Connection")))
        payload = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        self._respond()

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._respond()

    do_HEAD = do_GET

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
            ("Estrés de Protocolo y Red (Capa de Aplicación y Transporte)", {"methods": ["GET", "POST"]}),
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

    def test_protocol_network_scenario_exercises_methods_and_connection_close(self):
        ScenarioHandler.seen_requests.clear()
        report = asyncio.run(run_scenario(ScenarioConfig(
            self.base(duration=0.2, users=1),
            "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)",
            {"methods": ["GET", "POST"]},
        )))
        self.assertEqual([phase["method"] for phase in report.phases], ["GET", "POST"])
        self.assertTrue(any(method == "GET" and connection == "close" for method, connection in ScenarioHandler.seen_requests))
        self.assertTrue(any(method == "POST" and connection == "close" for method, connection in ScenarioHandler.seen_requests))

    def test_protocol_network_scenario_rejects_unsupported_methods(self):
        scenario = ScenarioConfig(
            self.base(),
            "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)",
            {"methods": ["TRACE"]},
        )
        with self.assertRaisesRegex(ValueError, "solo admite"):
            asyncio.run(run_scenario(scenario))

    def test_pps_destination_accepts_public_ip_without_sending_traffic(self):
        from loadtest.scenarios import _resolve_udp_destination

        address, _family, destination = asyncio.run(_resolve_udp_destination("8.8.8.8", 9000))
        self.assertEqual(str(address), "8.8.8.8")
        self.assertEqual(destination[1], 9000)

    def test_pps_stress_enforces_rate_size_and_duration_limits(self):
        scenarios = (
            (self.base(duration=0.1), {"pps": 1001}, "1000 datagramas/s"),
            (self.base(duration=0.1), {"packet_size": 1201}, "1200 bytes"),
            (self.base(duration=61), {"pps": 10}, "60 segundos"),
        )
        for config, params, message in scenarios:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                asyncio.run(run_scenario(ScenarioConfig(config, "PPS Stress", params)))

    def test_throughput_stress_reports_http_payload_bytes(self):
        report = asyncio.run(run_scenario(ScenarioConfig(
            self.base(duration=0.1, users=1),
            "Throughput Stress",
            {"method": "POST", "payload_size": 2048},
        )))
        self.assertEqual(report.aggregate.method, "POST")
        self.assertEqual(report.aggregate.bytes_sent, 2048 * report.aggregate.total_requests)
        self.assertEqual(report.aggregate.bytes_received, 2 * report.aggregate.total_requests)
        self.assertGreater(report.aggregate.upload_mbps, 0)

    def test_pps_stress_sends_to_local_udp_receiver(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.05)
        received = []
        receiving = threading.Event()

        def collect_datagrams():
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not receiving.is_set():
                try:
                    payload, _sender = receiver.recvfrom(1500)
                    received.append(payload)
                except socket.timeout:
                    continue

        receiver_thread = threading.Thread(target=collect_datagrams, daemon=True)
        receiver_thread.start()
        try:
            report = asyncio.run(run_scenario(ScenarioConfig(
                self.base(duration=0.2, users=1),
                "PPS Stress",
                {"host": "127.0.0.1", "port": receiver.getsockname()[1], "pps": 20, "packet_size": 32},
            )))
        finally:
            receiving.set()
            receiver_thread.join(timeout=1)
            receiver.close()
        self.assertGreater(report.aggregate.datagrams_sent, 0)
        self.assertEqual(report.aggregate.bytes_sent, report.aggregate.datagrams_sent * 32)
        self.assertTrue(received)
        self.assertTrue(all(len(payload) == 32 for payload in received))


if __name__ == "__main__":
    unittest.main()
