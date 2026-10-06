"""Interfaz gráfica Tkinter para ejecutar pruebas de carga sin bloquear la ventana."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .charts import generate_charts
from .gui_config import GuiValues, config_from_values
from .runner import LoadConfig, LoadReport, RequestResult, report_json, run_load


class LoadTestApp(tk.Tk):
    """Ventana principal con configuración, control, métricas y gráficos."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Load Test Tool")
        self.geometry("1060x760")
        self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._last_report: LoadReport | None = None
        self._last_report_path: Path | None = None
        self._latency_history: list[float] = []
        self._status_counts: dict[str, int] = {}
        self._build_variables()
        self._build_style()
        self._build_layout()
        self.after(100, self._drain_events)

    def _build_variables(self) -> None:
        self.url_var = tk.StringVar(value="http://localhost:8000/health")
        self.method_var = tk.StringVar(value="GET")
        self.users_var = tk.StringVar(value="10")
        self.duration_var = tk.StringVar(value="30")
        self.ramp_var = tk.StringVar(value="0")
        self.timeout_var = tk.StringVar(value="10")
        self.tls_var = tk.BooleanVar(value=True)
        self.report_var = tk.StringVar(value=str(Path("reports/load-report.json")))
        self.chart_dir_var = tk.StringVar(value="reports/charts")
        self.format_var = tk.StringVar(value="png")
        self.auto_charts_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Listo para ejecutar")
        self.request_var = tk.StringVar(value="Solicitudes: 0")
        self.rps_var = tk.StringVar(value="RPS: —")
        self.error_var = tk.StringVar(value="Errores: 0")
        self.latency_var = tk.StringVar(value="Latencia p95: —")
        self.progress_var = tk.DoubleVar(value=0)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("TkDefaultFont", 16, "bold"))
        style.configure("Section.TLabelframe.Label", font=("TkDefaultFont", 10, "bold"))
        style.configure("Metric.TLabel", font=("TkDefaultFont", 11, "bold"))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="Pruebas de carga", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Configura el escenario, ejecútalo en segundo plano y revisa el informe visual.").pack(anchor="w", pady=(2, 12))

        main = ttk.PanedWindow(root, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main, padding=(0, 0, 10, 0))
        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        main.add(left, weight=1)
        main.add(right, weight=1)
        self._build_config_panel(left)
        self._build_results_panel(right)

    def _build_config_panel(self, parent: ttk.Frame) -> None:
        endpoint = ttk.LabelFrame(parent, text="1. Endpoint y concurrencia", style="Section.TLabelframe", padding=10)
        endpoint.pack(fill="x", pady=(0, 10))
        self._field(endpoint, "URL", self.url_var, 0, 0, 3)
        ttk.Label(endpoint, text="Método").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(endpoint, textvariable=self.method_var, values=("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"), state="readonly", width=10).grid(row=3, column=0, sticky="ew", padx=(0, 8))
        self._field(endpoint, "Usuarios", self.users_var, 2, 1)
        self._field(endpoint, "Duración (s)", self.duration_var, 2, 2)
        self._field(endpoint, "Rampa (s)", self.ramp_var, 4, 0)
        self._field(endpoint, "Timeout (s)", self.timeout_var, 4, 1)
        endpoint.columnconfigure(1, weight=1)
        endpoint.columnconfigure(2, weight=1)
        endpoint.columnconfigure(3, weight=1)

        request = ttk.LabelFrame(parent, text="2. Petición", style="Section.TLabelframe", padding=10)
        request.pack(fill="both", expand=True, pady=(0, 10))
        ttk.Label(request, text="Cabeceras (una por línea: Nombre: valor)").pack(anchor="w")
        self.headers_text = tk.Text(request, height=5, wrap="none", undo=True)
        self.headers_text.pack(fill="x", pady=(4, 8))
        ttk.Label(request, text="Cuerpo de la petición (opcional)").pack(anchor="w")
        self.body_text = tk.Text(request, height=8, wrap="none", undo=True)
        self.body_text.pack(fill="both", expand=True, pady=(4, 0))

        output = ttk.LabelFrame(parent, text="3. Salida", style="Section.TLabelframe", padding=10)
        output.pack(fill="x")
        self._path_field(output, "Informe JSON", self.report_var, self._choose_report, 0)
        self._path_field(output, "Directorio gráficos", self.chart_dir_var, self._choose_chart_dir, 2)
        ttk.Label(output, text="Formato").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(output, textvariable=self.format_var, values=("png", "svg"), state="readonly", width=8).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(output, text="Generar gráficos automáticamente al terminar", variable=self.auto_charts_var).grid(row=5, column=1, columnspan=2, sticky="w", padx=(10, 0))
        ttk.Checkbutton(output, text="Verificar certificado TLS", variable=self.tls_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(7, 0))

        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(12, 0))
        self.start_button = ttk.Button(controls, text="▶  Iniciar prueba", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="■  Detener", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.chart_button = ttk.Button(controls, text="Generar gráficos desde JSON", command=self._generate_from_json)
        self.chart_button.pack(side="right")

    def _build_results_panel(self, parent: ttk.Frame) -> None:
        status = ttk.LabelFrame(parent, text="Ejecución", style="Section.TLabelframe", padding=10)
        status.pack(fill="x", pady=(0, 10))
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w")
        ttk.Progressbar(status, variable=self.progress_var, maximum=100, mode="determinate").pack(fill="x", pady=(8, 0))

        metrics = ttk.LabelFrame(parent, text="Métricas en vivo", style="Section.TLabelframe", padding=10)
        metrics.pack(fill="x", pady=(0, 10))
        for column, variable in enumerate((self.request_var, self.rps_var, self.error_var, self.latency_var)):
            ttk.Label(metrics, textvariable=variable, style="Metric.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 14))
            metrics.columnconfigure(column, weight=1)

        dashboard = ttk.LabelFrame(parent, text="Dashboard de ejecución", style="Section.TLabelframe", padding=8)
        dashboard.pack(fill="x", pady=(0, 10))
        self.dashboard_canvas = tk.Canvas(
            dashboard, height=220, background="#ffffff", highlightthickness=1,
            highlightbackground="#d8dee9",
        )
        self.dashboard_canvas.pack(fill="x", expand=True)
        self.dashboard_canvas.bind("<Configure>", lambda _event: self._draw_dashboard())

        report_frame = ttk.LabelFrame(parent, text="Informe final", style="Section.TLabelframe", padding=10)
        report_frame.pack(fill="both", expand=True)
        self.report_text = tk.Text(report_frame, state="disabled", wrap="word", background="#f7f7f7")
        self.report_text.pack(fill="both", expand=True)
        log_frame = ttk.LabelFrame(parent, text="Registro", style="Section.TLabelframe", padding=8)
        log_frame.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=5, state="disabled", wrap="word", background="#f7f7f7")
        self.log_text.pack(fill="x")

    @staticmethod
    def _field(parent: ttk.Widget, label: str, variable: tk.StringVar, row: int, column: int, span: int = 1) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, columnspan=span, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable).grid(row=row + 1, column=column, columnspan=span, sticky="ew", padx=(0, 8))

    def _path_field(self, parent: ttk.Widget, label: str, variable: tk.StringVar, command: Any, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=variable).grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=(0, 6))
        ttk.Button(parent, text="Examinar…", command=command).grid(row=row + 1, column=2, sticky="e")
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

    def _choose_report(self) -> None:
        path = filedialog.asksaveasfilename(title="Guardar informe JSON", defaultextension=".json", filetypes=(("JSON", "*.json"), ("Todos", "*.*")))
        if path:
            self.report_var.set(path)

    def _choose_chart_dir(self) -> None:
        path = filedialog.askdirectory(title="Seleccionar directorio de gráficos")
        if path:
            self.chart_dir_var.set(path)

    def _values(self) -> GuiValues:
        return GuiValues(
            url=self.url_var.get(), method=self.method_var.get(), users=self.users_var.get(),
            duration=self.duration_var.get(), ramp_up=self.ramp_var.get(), timeout=self.timeout_var.get(),
            headers=self.headers_text.get("1.0", "end"), body=self.body_text.get("1.0", "end-1c"),
            verify_tls=self.tls_var.get(), report_path=self.report_var.get(), chart_dir=self.chart_dir_var.get(),
            chart_format=self.format_var.get(), auto_charts=self.auto_charts_var.get(),
        )

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        try:
            config = config_from_values(self._values())
        except ValueError as exc:
            messagebox.showerror("Configuración inválida", str(exc))
            return
        self._prepare_run()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._worker_run, args=(config,), daemon=True)
        self._worker.start()

    def _prepare_run(self) -> None:
        self._last_report = None
        self._latency_history.clear()
        self._status_counts.clear()
        self.progress_var.set(0)
        self.request_var.set("Solicitudes: 0")
        self.rps_var.set("RPS: —")
        self.error_var.set("Errores: 0")
        self.latency_var.set("Latencia p95: —")
        self.status_var.set("Ejecutando…")
        self._set_text(self.report_text, "")
        self._draw_dashboard()
        self._append_log("Inicio de la prueba")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.chart_button.configure(state="disabled")

    def _worker_run(self, config: LoadConfig) -> None:
        assert self._stop_event is not None
        try:
            report = asyncio.run(run_load(config, on_progress=lambda result: self._events.put(("progress", result)), stop_event=self._stop_event))
            self._events.put(("complete", report))
        except Exception as exc:  # la GUI debe recibir cualquier fallo del worker
            self._events.put(("error", exc))

    def _stop(self) -> None:
        if self._stop_event and self._worker and self._worker.is_alive():
            self._stop_event.set()
            self.status_var.set("Deteniendo después de la petición en curso…")
            self._append_log("Solicitud de detención enviada")
            self.stop_button.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "progress":
                    self._update_progress(payload)
                elif event == "complete":
                    self._complete(payload)
                elif event == "error":
                    self._failed(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _update_progress(self, result: RequestResult) -> None:
        current = int(self.request_var.get().split(":", 1)[1].strip()) + 1
        self._latency_history.append(result.latency_ms)
        self._latency_history = self._latency_history[-120:]
        status = str(result.status) if result.status is not None else "ERROR"
        self._status_counts[status] = self._status_counts.get(status, 0) + 1
        self.request_var.set(f"Solicitudes: {current}")
        self.error_var.set(f"Errores: {1 if not result.ok else 0} en la última")
        self.latency_var.set(f"Última latencia: {result.latency_ms:.1f} ms")
        self._draw_dashboard()
        self._append_log(f"HTTP {result.status or 'ERROR'} · {result.latency_ms:.1f} ms")

    def _draw_dashboard(self) -> None:
        """Dibuja el dashboard sin dependencias externas y desde el hilo de Tk."""
        if not hasattr(self, "dashboard_canvas"):
            return
        canvas = self.dashboard_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 520)
        height = max(canvas.winfo_height(), 180)
        split = int(width * 0.62)
        canvas.create_text(16, 16, anchor="w", text="Latencia por solicitud", fill="#25324b", font=("TkDefaultFont", 10, "bold"))
        canvas.create_text(split + 18, 16, anchor="w", text="Respuestas HTTP", fill="#25324b", font=("TkDefaultFont", 10, "bold"))
        left_x, left_y, left_w, left_h = 18, 34, split - 34, height - 52
        right_x, right_y, right_w, right_h = split + 18, 42, width - split - 34, height - 60
        for y_ratio in (0, 0.5, 1):
            y = left_y + left_h * y_ratio
            canvas.create_line(left_x, y, left_x + left_w, y, fill="#e5e7eb")
        history = self._latency_history
        if history:
            maximum = max(max(history), 1.0)
            points = []
            for index, value in enumerate(history):
                x = left_x + (index / max(len(history) - 1, 1)) * left_w
                y = left_y + left_h - (value / maximum) * left_h
                points.extend((x, y))
            if len(points) >= 4:
                canvas.create_line(*points, fill="#2563eb", width=2, smooth=True)
            canvas.create_text(left_x + left_w, left_y + left_h + 12, anchor="e", text=f"última: {history[-1]:.1f} ms", fill="#64748b", font=("TkDefaultFont", 8))
            canvas.create_text(left_x, left_y - 7, anchor="w", text=f"máx. {maximum:.1f} ms", fill="#64748b", font=("TkDefaultFont", 8))
        else:
            canvas.create_text(left_x + left_w / 2, left_y + left_h / 2, text="Inicia una prueba para ver la latencia", fill="#94a3b8")
        statuses = self._status_counts
        if statuses:
            maximum = max(statuses.values())
            bar_gap = max(4, right_h // max(len(statuses), 1) // 4)
            bar_h = max(12, min(25, (right_h - bar_gap * (len(statuses) - 1)) // len(statuses)))
            for index, (status, count) in enumerate(sorted(statuses.items())):
                y = right_y + index * (bar_h + bar_gap)
                bar_w = (count / maximum) * max(right_w - 64, 1)
                color = "#16a34a" if status[:1] in {"2", "3"} else "#dc2626"
                canvas.create_text(right_x, y + bar_h / 2, anchor="w", text=status, fill="#475569", font=("TkDefaultFont", 9))
                canvas.create_rectangle(right_x + 42, y, right_x + 42 + bar_w, y + bar_h, fill=color, outline="")
                canvas.create_text(right_x + 48 + bar_w, y + bar_h / 2, anchor="w", text=str(count), fill="#475569", font=("TkDefaultFont", 9))
        else:
            canvas.create_text(right_x + right_w / 2, right_y + right_h / 2, text="Sin respuestas todavía", fill="#94a3b8")

    def _complete(self, report: LoadReport) -> None:
        self._last_report = report
        values = self._values()
        report_path = Path(values.report_path).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_json(report) + "\n", encoding="utf-8")
        self._last_report_path = report_path
        self.status_var.set("Prueba detenida" if self._stop_event and self._stop_event.is_set() else "Prueba completada")
        self.progress_var.set(100)
        self.rps_var.set(f"RPS: {report.requests_per_second:.2f}")
        self.error_var.set(f"Errores: {report.failed_requests} ({report.error_rate_percent:.2f}%)")
        self.latency_var.set(f"Latencia p95: {report.latency_ms['p95']} ms")
        self._set_text(self.report_text, json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        self._append_log(f"Informe guardado en {report_path}")
        if values.auto_charts:
            self._generate_charts(report_path, values.chart_dir, values.chart_format)
        self._finish_controls()

    def _failed(self, error: Exception) -> None:
        self.status_var.set("Error en la ejecución")
        self._append_log(f"ERROR: {error}")
        messagebox.showerror("Error de ejecución", str(error))
        self._finish_controls()

    def _finish_controls(self) -> None:
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.chart_button.configure(state="normal")

    def _generate_from_json(self) -> None:
        path = filedialog.askopenfilename(title="Abrir informe JSON", filetypes=(("JSON", "*.json"), ("Todos", "*.*")))
        if path:
            self._generate_charts(Path(path), self.chart_dir_var.get(), self.format_var.get())

    def _generate_charts(self, report_path: Path, chart_dir: str, chart_format: str) -> None:
        try:
            outputs = generate_charts(report_path, chart_dir, chart_format)
        except (OSError, RuntimeError, ValueError) as exc:
            self._append_log(f"No se pudieron generar gráficos: {exc}")
            messagebox.showerror("Gráficos", str(exc))
            return
        self._append_log(f"Gráficos generados: {len(outputs)} archivos en {chart_dir}")
        if messagebox.askyesno("Gráficos listos", "¿Quieres abrir el directorio de gráficos?"):
            webbrowser.open(Path(chart_dir).resolve().as_uri())

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno("Prueba en curso", "¿Detener la prueba y cerrar la ventana?"):
                return
            if self._stop_event:
                self._stop_event.set()
        self.destroy()


def main() -> int:
    app = LoadTestApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
