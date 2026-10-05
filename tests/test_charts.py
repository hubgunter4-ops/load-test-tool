import json
import tempfile
import unittest
from pathlib import Path

from loadtest.charts import generate_charts, load_report


REPORT = {
    "url": "http://localhost:8000/health",
    "method": "GET",
    "users": 4,
    "duration_seconds": 2.0,
    "total_requests": 100,
    "successful_requests": 96,
    "failed_requests": 4,
    "requests_per_second": 50.0,
    "error_rate_percent": 4.0,
    "latency_ms": {"min": 2.0, "avg": 8.0, "p50": 6.0, "p95": 18.0, "p99": 25.0, "max": 31.0},
    "status_codes": {"200": 96, "500": 4},
    "errors": {"HTTP 500": 4},
}


class ChartsTests(unittest.TestCase):
    def test_load_report_validates_json(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            source.write_text(json.dumps(REPORT), encoding="utf-8")
            self.assertEqual(load_report(source)["total_requests"], 100)

    def test_generates_png_charts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            output = Path(directory) / "charts"
            source.write_text(json.dumps(REPORT), encoding="utf-8")
            created = generate_charts(source, output)
            self.assertEqual({path.name for path in created}, {"latency.png", "status_codes.png", "summary.png"})
            self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in created))

    def test_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            source.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_report(source)


if __name__ == "__main__":
    unittest.main()
