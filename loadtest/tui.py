"""Interfaz de terminal para configurar y ejecutar pruebas de carga."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ProgressBar, RichLog, Select, Static, TextArea

from .cli import _run_chart
from .charts import generate_charts
from .external_tools import EXECUTION_ENGINES, plan
from .gui_config import GuiValues, external_config_from_values, scenario_from_values
from .runner import RequestResult
from .scenarios import SCENARIO_MODES, SCENARIO_PARAMS_EXAMPLES, ScenarioReport, run_scenario, scenario_json


class LoadTestTui(App[None]):
    """Configuración, ejecución y resultados en una sola pantalla de terminal."""

    TITLE = "Load Test Tool"
    SUB_TITLE = "HTTP load testing"
    CSS = """
    Screen {
        background: #101820;
        color: #e7eee9;
    }
    #workspace {
        height: 1fr;
        padding: 0 1;
    }
    #config, #results {
        width: 1fr;
        height: 1fr;
        border: round #3b7772;
        padding: 1 2;
        margin: 0 1;
    }
    .section-title {
        color: #79d2bb;
        text-style: bold;
        margin: 0 0 1 0;
    }
    .field-label {
        color: #b8c8c3;
        margin: 1 0 0 0;
    }
    #numeric-fields {
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-gutter: 0 1;
        height: auto;
    }
    #numeric-fields Input {
        width: 1fr;
    }
    #request-fields {
        height: auto;
    }
    #request-fields TextArea {
        height: 5;
        margin-bottom: 1;
    }
    #scenario-params {
        height: 5;
    }
    #throughput-fields, #pps-fields {
        display: none;
        height: auto;
        margin-bottom: 1;
    }
    #external-fields {
        display: none;
        height: auto;
        margin-bottom: 1;
    }
    #scenario-note {
        color: #f2b872;
        height: auto;
        margin-bottom: 1;
    }
    #actions {
        height: auto;
        margin-top: 1;
    }
    #actions Button {
        margin-right: 1;
    }
    #status {
        color: #f2b872;
        text-style: bold;
        margin-bottom: 1;
    }
    #metrics {
        color: #79d2bb;
        height: auto;
        margin: 1 0;
    }
    #report-path, #chart-path {
        width: 1fr;
    }
    #report-output {
        height: 1fr;
        border: round #30413e;
        background: #0b1115;
    }
    #event-log {
        height: 10;
        border: round #30413e;
        background: #0b1115;
        margin-top: 1;
    }
    """

    BINDINGS = [("ctrl+c", "request_quit", "Salir")]

    def __init__(self) -> None:
        super().__init__()
        self._stop_event: threading.Event | None = None
        self._test_running = False
        self._started_at = 0.0
        self._active_scenario = SCENARIO_MODES[0]
        self._request_count = 0
        self._failure_count = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._latencies: list[float] = []
        self._status_counts: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            with VerticalScroll(id="config"):
                yield Label("PRUEBA", classes="section-title")
                with Horizontal(id="actions"):
                    yield Button("▶ Ejecutar", id="start", variant="success")
                    yield Button("■ Detener", id="stop", variant="error", disabled=True)
                yield Label("Endpoint", classes="field-label")
                yield Input("http://localhost:8000/health", id="url", placeholder="https://host/ruta")
                yield Label("Método", classes="field-label")
                yield Select.from_values(("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"), value="GET", id="method")
                with Grid(id="numeric-fields"):
                    yield Input("10", id="users", type="integer", placeholder="Usuarios")
                    yield Input("30", id="duration", type="number", placeholder="Duración (s)")
                    yield Input("0", id="ramp-up", type="number", placeholder="Rampa (s)")
                    yield Input("10", id="timeout", type="number", placeholder="Timeout (s)")
                yield Label("Motor de ejecución", classes="field-label")
                yield Select.from_values(EXECUTION_ENGINES, value="Integrado", id="engine")
                yield Label("Escenario", classes="field-label")
                yield Select.from_values(SCENARIO_MODES, value=SCENARIO_MODES[0], id="scenario")
                yield Label("Parámetros del escenario (JSON)", id="scenario-params-label", classes="field-label")
                yield TextArea(SCENARIO_PARAMS_EXAMPLES[SCENARIO_MODES[0]], id="scenario-params")
                with Vertical(id="throughput-fields"):
                    yield Label("Método para subida", classes="field-label")
                    yield Select.from_values(("POST", "PUT", "PATCH"), value="POST", id="throughput-method")
                    yield Label("Payload HTTP (bytes)", classes="field-label")
                    yield Input("262144", id="throughput-payload-size", type="integer")
                with Vertical(id="pps-fields"):
                    yield Label("Destino UDP (IP o DNS)", classes="field-label")
                    yield Input("127.0.0.1", id="pps-host", placeholder="servidor autorizado")
                    with Grid(id="pps-numeric-fields"):
                        yield Input("9000", id="pps-port", type="integer", placeholder="Puerto")
                        yield Input("100", id="pps-rate", type="integer", placeholder="Datagramas/s")
                        yield Input("512", id="pps-packet-size", type="integer", placeholder="Bytes/datagrama")
                with Grid(id="external-fields"):
                    yield Label("Hilos", classes="field-label")
                    yield Input("2", id="external-threads", type="integer")
                    yield Label("Tasa RPS/PPS/Kbit/s", classes="field-label")
                    yield Input("100", id="external-rate", type="integer")
                    yield Label("Interfaz Pktgen", classes="field-label")
                    yield Input("", id="external-interface", placeholder="eth0")
                    yield Label("Destino Pktgen", classes="field-label")
                    yield Input("", id="external-destination", placeholder="198.18.0.2")
                    yield Label("Protocolo", classes="field-label")
                    yield Select.from_values(("tcp", "udp"), value="tcp", id="external-protocol")
                    yield Label("Paquetes Pktgen", classes="field-label")
                    yield Input("1000", id="external-count", type="integer")
                yield Static("", id="scenario-note")
                with Vertical(id="request-fields"):
                    yield Label("Tipo de target", classes="field-label")
                    yield Select.from_values(("Auto", "REST API", "GraphQL", "Health Check", "Upload"), value="Auto", id="target-type")
                    yield Label("Cabeceras (una por línea: Nombre: valor)", classes="field-label")
                    yield TextArea("", id="headers")
                    yield Label("Cuerpo de la petición", classes="field-label")
                    yield TextArea("", id="body")
                yield Label("Informe JSON", classes="field-label")
                yield Input("reports/load-report.json", id="report-path")
                yield Label("Directorio de gráficos", classes="field-label")
                yield Input("reports/charts", id="chart-path")
                yield Label("Formato de gráficos", classes="field-label")
                yield Select.from_values(("png", "svg"), value="png", id="chart-format")
                yield Checkbox("Verificar TLS", value=True, id="verify-tls")
                yield Checkbox("Generar gráficos al finalizar", value=False, id="auto-charts")
            with Vertical(id="results"):
                yield Label("EJECUCIÓN", classes="section-title")
                yield Static("Listo para ejecutar", id="status")
                yield ProgressBar(total=100, show_eta=False, id="progress")
                yield Static("Solicitudes: 0   RPS: —   Errores: 0   p95: —", id="metrics")
                yield Label("INFORME", classes="section-title")
                yield TextArea("El informe aparecerá aquí al terminar.", read_only=True, id="report-output")
                yield Label("EVENTOS", classes="section-title")
                yield RichLog(id="event-log", markup=False, wrap=True)
        yield Footer()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.start_run()
        elif event.button.id == "stop":
            self.stop_run()

    @on(Select.Changed, "#scenario")
    def on_scenario_changed(self, event: Select.Changed) -> None:
        scenario = str(event.value)
        example = SCENARIO_PARAMS_EXAMPLES.get(scenario, "{}")
        self.query_one("#scenario-params", TextArea).text = example
        is_throughput = scenario == "Throughput Stress"
        is_pps = scenario == "PPS Stress"
        self.query_one("#scenario-params", TextArea).display = not (is_throughput or is_pps)
        self.query_one("#scenario-params-label", Label).display = not (is_throughput or is_pps)
        self.query_one("#throughput-fields", Vertical).display = is_throughput
        self.query_one("#pps-fields", Vertical).display = is_pps
        notes = {
            "Throughput Stress": "Mbps mide bytes del payload HTTP; usa POST/PUT para estresar subida.",
            "PPS Stress": "UDP admite destinos salientes autorizados. Máximo 1.000 PPS, 1.200 bytes y 60 s.",
        }
        self.query_one("#scenario-note", Static).update(notes.get(scenario, ""))

    @on(Select.Changed, "#engine")
    def on_engine_changed(self, event: Select.Changed) -> None:
        self.query_one("#external-fields", Grid).display = str(event.value) != "Integrado"

    @on(Select.Changed, "#target-type")
    def on_target_type_changed(self, event: Select.Changed) -> None:
        target = str(event.value or "Auto")
        if target == "Auto":
            return
        if self.query_one("#headers", TextArea).text.strip():
            return
        from .runner import target_header_preset
        preset = target_header_preset(target)
        lines = [f"{key}: {value}" for key, value in preset.items()]
        self.query_one("#headers", TextArea).text = "\n".join(lines)

    def start_run(self) -> None:
        if self._test_running:
            return
        values = self._values()
        if values.execution_engine != "Integrado":
            try:
                external = external_config_from_values(values)
                result = plan(external)
                report_path = Path(values.report_path).expanduser()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                rendered = json.dumps(result, indent=2, ensure_ascii=False)
                report_path.write_text(rendered + "\n", encoding="utf-8")
                self.query_one("#report-output", TextArea).text = rendered
                self.query_one("#status", Static).update("Plan externo generado; no se ejecutó tráfico")
                self.query_one("#event-log", RichLog).write(f"Plan {values.execution_engine} guardado en {report_path}")
            except (ValueError, OSError) as exc:
                self.query_one("#status", Static).update(f"Configuración inválida: {exc}")
            return
        try:
            config = scenario_from_values(values)
        except ValueError as exc:
            self.query_one("#status", Static).update(f"Configuración inválida: {exc}")
            return
        self._test_running = True
        self._active_scenario = str(self.query_one("#scenario", Select).value)
        self._stop_event = threading.Event()
        self._started_at = time.perf_counter()
        self._request_count = 0
        self._failure_count = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._latencies.clear()
        self._status_counts.clear()
        self.query_one("#progress", ProgressBar).update(progress=0)
        self.query_one("#report-output", TextArea).text = "Ejecutando…"
        self.query_one("#status", Static).update("Ejecutando escenario…")
        self.query_one("#event-log", RichLog).write("Inicio de la prueba")
        self.query_one("#start", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        self.run_worker(self._execute(config), name="load-test", exclusive=True)

    def stop_run(self) -> None:
        if self._stop_event and self._test_running:
            self._stop_event.set()
            self.query_one("#status", Static).update("Deteniendo después de la petición en curso…")
            self.query_one("#event-log", RichLog).write("Solicitud de detención enviada")
            self.query_one("#stop", Button).disabled = True

    async def _execute(self, config) -> None:
        try:
            assert self._stop_event is not None
            report = await run_scenario(
                config,
                on_progress=self._on_progress,
                on_phase=self._on_phase,
                stop_event=self._stop_event,
            )
            await self._complete(report)
        except Exception as exc:
            self.query_one("#status", Static).update(f"Error: {exc}")
            self.query_one("#event-log", RichLog).write(f"ERROR: {exc}")
            self._finish()

    def _on_phase(self, name: str, index: int, total: int) -> None:
        self.query_one("#status", Static).update(f"Fase {index}/{total}: {name}")
        self.query_one("#progress", ProgressBar).update(progress=(index - 1) * 100 / max(total, 1))
        self.query_one("#event-log", RichLog).write(f"Fase {index}/{total}: {name}")

    def _on_progress(self, result: RequestResult) -> None:
        self._request_count += result.request_count
        self._failure_count += not result.ok
        self._bytes_sent += result.bytes_sent
        self._bytes_received += result.bytes_received
        if self._active_scenario != "PPS Stress":
            self._latencies.append(result.latency_ms)
            self._latencies = self._latencies[-120:]
            status = str(result.status) if result.status is not None else "ERROR"
            self._status_counts[status] = self._status_counts.get(status, 0) + 1
        elapsed = max(time.perf_counter() - self._started_at, 0.001)
        if self._active_scenario == "PPS Stress":
            self.query_one("#metrics", Static).update(
                f"Datagramas enviados: {self._request_count}   PPS: {self._request_count / elapsed:.1f}   "
                f"TX: {self._bytes_sent * 8 / elapsed / 1_000_000:.4f} Mbps"
            )
            self.query_one("#event-log", RichLog).write(f"UDP: +{result.request_count} datagramas enviados")
        elif self._active_scenario == "Throughput Stress":
            self.query_one("#metrics", Static).update(
                f"Solicitudes: {self._request_count}   TX: {self._bytes_sent * 8 / elapsed / 1_000_000:.4f} Mbps   "
                f"RX: {self._bytes_received * 8 / elapsed / 1_000_000:.4f} Mbps"
            )
            self.query_one("#event-log", RichLog).write(
                f"HTTP {result.status} · {result.bytes_sent} B TX / {result.bytes_received} B RX"
            )
        else:
            p95 = sorted(self._latencies)[int((len(self._latencies) - 1) * 0.95)]
            self.query_one("#metrics", Static).update(
                f"Solicitudes: {self._request_count}   RPS: {self._request_count / elapsed:.1f}   "
                f"Errores: {self._failure_count}   p95 reciente: {p95:.1f} ms"
            )
            self.query_one("#event-log", RichLog).write(
                f"HTTP {result.status or 'ERROR'} · {result.latency_ms:.1f} ms"
            )

    async def _complete(self, scenario_report: ScenarioReport) -> None:
        report_path = Path(self.query_one("#report-path", Input).value).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(scenario_json(scenario_report) + "\n", encoding="utf-8")
        report = scenario_report.aggregate
        output = json.dumps(scenario_report.to_dict(), indent=2, ensure_ascii=False)
        self.query_one("#report-output", TextArea).text = output
        stopped = self._stop_event is not None and self._stop_event.is_set()
        self.query_one("#status", Static).update("Prueba detenida" if stopped else "Prueba completada")
        self.query_one("#progress", ProgressBar).update(progress=100)
        if self._active_scenario == "PPS Stress":
            metrics = (
                f"UDP enviados: {report.datagrams_sent}   PPS: {report.packets_per_second:.2f}   "
                f"TX: {report.upload_mbps:.4f} Mbps"
            )
        else:
            metrics = (
                f"Solicitudes: {report.total_requests}   RPS: {report.requests_per_second:.2f}   "
                f"TX: {report.upload_mbps:.4f} Mbps   RX: {report.download_mbps:.4f} Mbps   "
                f"Errores: {report.failed_requests} ({report.error_rate_percent:.2f}%)"
            )
        self.query_one("#metrics", Static).update(metrics)
        self.query_one("#event-log", RichLog).write(f"Informe guardado en {report_path}")
        if self.query_one("#auto-charts", Checkbox).value:
            try:
                outputs = await asyncio.to_thread(
                    generate_charts,
                    report_path,
                    self.query_one("#chart-path", Input).value,
                    str(self.query_one("#chart-format", Select).value),
                )
                self.query_one("#event-log", RichLog).write(f"Gráficos generados: {len(outputs)}")
            except (OSError, RuntimeError, ValueError) as exc:
                self.query_one("#event-log", RichLog).write(f"No se pudieron generar gráficos: {exc}")
        self._finish()

    def _finish(self) -> None:
        self._test_running = False
        self.query_one("#start", Button).disabled = False
        self.query_one("#stop", Button).disabled = True

    def _values(self) -> GuiValues:
        return GuiValues(
            url=self.query_one("#url", Input).value,
            method=str(self.query_one("#method", Select).value),
            users=self.query_one("#users", Input).value,
            duration=self.query_one("#duration", Input).value,
            ramp_up=self.query_one("#ramp-up", Input).value,
            timeout=self.query_one("#timeout", Input).value,
            headers=self.query_one("#headers", TextArea).text,
            body=self.query_one("#body", TextArea).text,
            target_type=str(self.query_one("#target-type", Select).value),
            verify_tls=self.query_one("#verify-tls", Checkbox).value,
            report_path=self.query_one("#report-path", Input).value,
            chart_dir=self.query_one("#chart-path", Input).value,
            chart_format=str(self.query_one("#chart-format", Select).value),
            auto_charts=self.query_one("#auto-charts", Checkbox).value,
            scenario=str(self.query_one("#scenario", Select).value),
            scenario_params=self.query_one("#scenario-params", TextArea).text,
            throughput_method=str(self.query_one("#throughput-method", Select).value),
            throughput_payload_size=self.query_one("#throughput-payload-size", Input).value,
            pps_host=self.query_one("#pps-host", Input).value,
            pps_port=self.query_one("#pps-port", Input).value,
            pps_rate=self.query_one("#pps-rate", Input).value,
            pps_packet_size=self.query_one("#pps-packet-size", Input).value,
            execution_engine=str(self.query_one("#engine", Select).value),
            external_threads=self.query_one("#external-threads", Input).value,
            external_rate=self.query_one("#external-rate", Input).value,
            external_interface=self.query_one("#external-interface", Input).value,
            external_destination=self.query_one("#external-destination", Input).value,
            external_protocol=str(self.query_one("#external-protocol", Select).value),
            external_count=self.query_one("#external-count", Input).value,
        )

    def action_request_quit(self) -> None:
        self.stop_run()
        self.exit()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "chart":
        return _run_chart(argv[1:])
    if argv:
        print("El comando loadtest abre la TUI; para la CLI usa loadtest-cli.", file=sys.stderr)
        return 2
    LoadTestTui().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
