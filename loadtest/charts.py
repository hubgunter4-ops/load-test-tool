"""Generación de gráficos estáticos a partir de informes de carga."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_REQUIRED_KEYS = {
    "url",
    "method",
    "users",
    "duration_seconds",
    "total_requests",
    "successful_requests",
    "failed_requests",
    "requests_per_second",
    "error_rate_percent",
    "latency_ms",
    "status_codes",
}


def load_report(path: str | Path) -> dict[str, Any]:
    """Carga y valida un informe JSON generado por ``loadtest``."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"no existe el archivo JSON: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON inválido en {source}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("el informe debe contener un objeto JSON")
    missing = sorted(_REQUIRED_KEYS - data.keys())
    if missing:
        raise ValueError("faltan campos requeridos en el informe: " + ", ".join(missing))
    if not isinstance(data["latency_ms"], dict) or not isinstance(data["status_codes"], dict):
        raise ValueError("latency_ms y status_codes deben ser objetos JSON")
    return data


def _as_number(value: Any) -> float | None:
    return float(value) if value is not None else None


def generate_charts(report_path: str | Path, output_dir: str | Path = "charts", fmt: str = "png", dpi: int = 160) -> list[Path]:
    """Genera summary, latency y status_codes en PNG o SVG.

    Matplotlib se importa de forma diferida para conservar el CLI principal sin
    dependencias gráficas obligatorias.
    """
    if fmt not in {"png", "svg"}:
        raise ValueError("el formato debe ser png o svg")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("para generar gráficos instala la dependencia opcional: pip install -e '.[charts]'") from exc

    data = load_report(report_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")
    accent = "#2563eb"
    green = "#16a34a"
    red = "#dc2626"
    purple = "#7c3aed"
    title = f"{data['method']} {data['url']}"
    created: list[Path] = []

    latency = data["latency_ms"]
    latency_keys = ["min", "avg", "p50", "p95", "p99", "max"]
    latency_values = [_as_number(latency.get(key)) for key in latency_keys]
    valid_latency = [(key, value) for key, value in zip(latency_keys, latency_values) if value is not None]
    fig, ax = plt.subplots(figsize=(10, 5.8))
    if valid_latency:
        labels, values = zip(*valid_latency)
        bars = ax.bar(labels, values, color=[accent, purple, purple, red, red, red][:len(values)])
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        ax.set_ylabel("Milisegundos")
        ax.set_ylim(bottom=0)
    else:
        ax.text(0.5, 0.5, "Sin datos de latencia", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Latencia por percentil\n" + title, loc="left", fontweight="bold")
    fig.tight_layout()
    output = destination / f"latency.{fmt}"
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    created.append(output)

    statuses = data["status_codes"]
    labels = list(statuses.keys())
    values = [int(statuses[key]) for key in labels]
    fig, ax = plt.subplots(figsize=(8, 5.8))
    if values:
        colors = [green if key.startswith("2") or key.startswith("3") else red for key in labels]
        bars = ax.bar(labels, values, color=colors)
        ax.bar_label(bars, padding=3)
        ax.set_xlabel("Código HTTP")
        ax.set_ylabel("Solicitudes")
        ax.set_ylim(bottom=0)
    else:
        ax.text(0.5, 0.5, "Sin códigos HTTP", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Distribución de códigos HTTP\n" + title, loc="left", fontweight="bold")
    fig.tight_layout()
    output = destination / f"status_codes.{fmt}"
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    created.append(output)

    metric_labels = ["Total", "Exitosas", "Fallidas"]
    metric_values = [int(data["total_requests"]), int(data["successful_requests"]), int(data["failed_requests"])]
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.8), gridspec_kw={"width_ratios": [1.35, 1]})
    bars = axes[0].bar(metric_labels, metric_values, color=[accent, green, red])
    axes[0].bar_label(bars, padding=3)
    axes[0].set_ylabel("Solicitudes")
    axes[0].set_ylim(bottom=0)
    axes[0].set_title("Volumen", loc="left", fontweight="bold")
    summary_labels = ["RPS", "Error %", "Usuarios", "Duración s"]
    summary_values = [float(data["requests_per_second"]), float(data["error_rate_percent"]), float(data["users"]), float(data["duration_seconds"])]
    bars = axes[1].barh(summary_labels, summary_values, color=purple)
    axes[1].bar_label(bars, fmt="%.2f", padding=3)
    axes[1].set_title("Indicadores", loc="left", fontweight="bold")
    fig.suptitle("Resumen de la prueba\n" + title, x=0.06, ha="left", fontweight="bold")
    fig.tight_layout()
    output = destination / f"summary.{fmt}"
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    created.append(output)
    return created
