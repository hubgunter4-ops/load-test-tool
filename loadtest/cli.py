from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .runner import LoadConfig, LoadReport
from .scenarios import SCENARIO_MODES, ScenarioConfig, run_scenario, scenario_json


def _headers(values: list[str]) -> dict[str, str]:
    parsed = {}
    for value in values:
        if ":" not in value:
            raise argparse.ArgumentTypeError(f"cabecera inválida: {value!r}; usa Nombre: valor")
        name, content = value.split(":", 1)
        parsed[name.strip()] = content.strip()
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loadtest",
        description="Pruebas de carga HTTP concurrentes para medir capacidad y latencia del servidor.",
    )
    parser.add_argument("url", help="URL del endpoint a probar")
    parser.add_argument("-u", "--users", type=int, default=10, help="usuarios concurrentes (por defecto: 10)")
    parser.add_argument("-d", "--duration", type=float, default=30, help="duración en segundos (por defecto: 30)")
    parser.add_argument("--ramp-up", type=float, default=0, help="segundos para incorporar gradualmente a los usuarios")
    parser.add_argument("-X", "--method", default="GET", help="método HTTP (por defecto: GET)")
    parser.add_argument("-H", "--header", action="append", default=[], help="cabecera Nombre: valor; se puede repetir")
    parser.add_argument("--body", help="cuerpo de la petición como texto")
    parser.add_argument("--body-file", type=Path, help="archivo cuyo contenido será el cuerpo")
    parser.add_argument("--timeout", type=float, default=10, help="timeout por petición en segundos")
    parser.add_argument("--insecure", action="store_true", help="no verificar certificados TLS")
    parser.add_argument("-o", "--output", type=Path, help="guardar el informe JSON en este archivo")
    parser.add_argument("--scenario", choices=SCENARIO_MODES, default="Carga estándar", help="escenario de estrés a ejecutar")
    parser.add_argument("--scenario-params", default="{}", help="JSON; throughput: method/payload_size, PPS: host/port/pps/packet_size")
    return parser


def build_chart_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loadtest chart",
        description="Genera gráficos visuales a partir de un informe JSON de loadtest.",
    )
    parser.add_argument("report", type=Path, help="archivo JSON generado con --output")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("charts"), help="directorio de salida (por defecto: charts)")
    parser.add_argument("--format", choices=("png", "svg"), default="png", help="formato de salida (por defecto: png)")
    parser.add_argument("--dpi", type=int, default=160, help="resolución para PNG (por defecto: 160)")
    return parser


def _print_report(report: LoadReport) -> None:
    print(f"\nPrueba completada: {report.method} {report.url}")
    print(f"Usuarios: {report.users} | Duración: {report.duration_seconds:.2f}s | Peticiones: {report.total_requests}")
    print(f"RPS: {report.requests_per_second:.2f} | Éxito: {report.successful_requests} | Errores: {report.failed_requests} ({report.error_rate_percent:.2f}%)")
    print(f"Payload: {report.bytes_sent} B enviados / {report.bytes_received} B recibidos | TX: {report.upload_mbps:.4f} Mbps | RX: {report.download_mbps:.4f} Mbps")
    if report.method == "UDP":
        print(f"Datagramas enviados: {report.datagrams_sent} | PPS: {report.packets_per_second:.2f} (envío local; entrega no confirmada)")
    latency = report.latency_ms
    print("Latencia (ms): " + " | ".join(f"{key}={value}" for key, value in latency.items()))
    print("Códigos HTTP: " + (", ".join(f"{key}: {value}" for key, value in report.status_codes.items()) or "ninguno"))
    if report.errors:
        print("Errores: " + ", ".join(f"{key}: {value}" for key, value in report.errors.items()))


def _run_chart(argv: list[str]) -> int:
    args = build_chart_parser().parse_args(argv)
    try:
        from .charts import generate_charts
        outputs = generate_charts(args.report, args.output_dir, args.format, args.dpi)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error al generar gráficos: {exc}", file=sys.stderr)
        return 2
    print("Gráficos generados:")
    for output in outputs:
        print(f"- {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "chart":
        return _run_chart(argv[1:])
    args = build_parser().parse_args(argv)
    if args.body and args.body_file:
        print("Usa solo una de --body o --body-file", file=sys.stderr)
        return 2
    try:
        body = args.body.encode() if args.body is not None else (args.body_file.read_bytes() if args.body_file else None)
        config = LoadConfig(
            url=args.url,
            users=args.users,
            duration=args.duration,
            ramp_up=args.ramp_up,
            method=args.method,
            headers=_headers(args.header),
            body=body,
            timeout=args.timeout,
            verify_tls=not args.insecure,
        )
        try:
            scenario_params = json.loads(args.scenario_params or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"--scenario-params no es JSON válido: {exc.msg}") from exc
        if not isinstance(scenario_params, dict):
            raise ValueError("--scenario-params debe ser un objeto JSON")
        scenario_report = asyncio.run(run_scenario(ScenarioConfig(config, args.scenario, scenario_params)))
        report = scenario_report.aggregate
    except (OSError, ValueError, argparse.ArgumentTypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    if args.output:
        args.output.write_text(scenario_json(scenario_report) + "\n", encoding="utf-8")
        print(f"Informe JSON: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
