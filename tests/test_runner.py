import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from loadtest.runner import LoadConfig, percentile, report_json, run_load


class Handler(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):  # noqa: N802
        type(self).calls += 1
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class LoadTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/health"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_percentile_empty_and_interpolation(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([10, 20, 30, 40], 50), 25.0)
        self.assertEqual(percentile([10, 20, 30, 40], 99), 39.7)

    def test_run_load_collects_concurrent_results(self):
        report = asyncio.run(run_load(LoadConfig(self.url, users=4, duration=0.3)))
        self.assertGreater(report.total_requests, 0)
        self.assertEqual(report.total_requests, report.successful_requests)
        self.assertEqual(report.failed_requests, 0)
        self.assertEqual(report.status_codes, {"200": report.total_requests})
        self.assertIsNotNone(report.latency_ms["p95"])

    def test_report_is_valid_json(self):
        report = asyncio.run(run_load(LoadConfig(self.url, users=1, duration=0.1)))
        parsed = json.loads(report_json(report))
        self.assertEqual(parsed["method"], "GET")
        self.assertIn("latency_ms", parsed)

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            asyncio.run(run_load(LoadConfig("not-a-url", duration=0.1)))
        with self.assertRaises(ValueError):
            asyncio.run(run_load(LoadConfig(self.url, users=0, duration=0.1)))

    def test_stop_event_can_cancel_before_first_request(self):
        stop_event = threading.Event()
        stop_event.set()
        report = asyncio.run(run_load(LoadConfig(self.url, users=4, duration=0.3), stop_event=stop_event))
        self.assertEqual(report.total_requests, 0)


if __name__ == "__main__":
    unittest.main()
