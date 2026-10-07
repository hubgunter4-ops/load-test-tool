import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from loadtest.tui import LoadTestTui


class TuiExternalAdapterIntegrationTests(unittest.TestCase):
    def _run_selection(self, engine: str, expected_tool: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / f"{expected_tool}.json")

            async def exercise() -> dict:
                app = LoadTestTui()
                async with app.run_test(size=(140, 50)) as pilot:
                    app.query_one("#engine").value = engine
                    app.query_one("#url").value = "https://example.test/health"
                    app.query_one("#users").value = "8"
                    app.query_one("#duration").value = "12"
                    app.query_one("#external-threads").value = "2"
                    app.query_one("#external-rate").value = "25"
                    app.query_one("#report-path").value = report_path
                    await pilot.pause()
                    self.assertTrue(app.query_one("#external-fields").display)
                    await pilot.click("#start")
                    await pilot.pause()
                    self.assertIn("Plan externo generado", str(app.query_one("#status").render()))
                    return json.loads(Path(report_path).read_text(encoding="utf-8"))

            return asyncio.run(exercise())

    def test_wrk_selection_generates_plan_in_tui(self):
        report = self._run_selection("Wrk", "wrk")
        self.assertEqual(report["config"]["tool"], "wrk")
        self.assertEqual(report["command"][0], "wrk")
        self.assertFalse(report["loop"]["enabled"])

    def test_slowhttptest_selection_generates_plan_in_tui(self):
        report = self._run_selection("SlowHTTPTest", "slowhttptest")
        self.assertEqual(report["config"]["tool"], "slowhttptest")
        self.assertEqual(report["command"][0], "slowhttptest")
        self.assertIn("-u", report["command"])
        self.assertFalse(report["loop"]["enabled"])


if __name__ == "__main__":
    unittest.main()
