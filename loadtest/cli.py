from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .external_tools import ExternalConfig, ExternalToolError, execute, plan_json, tool_status
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


def build_external_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loadtest external",
        description="Planifica o ejecuta Wrk, Wrk2, Pktgen, SlowHTTPTest e iPerf3.",
        epilog="Por seguridad, solo planifica por defecto. Ejecuta únicamente con --execute --authorized.",
    )
    parser.add_argument("--tool", choices=("wrk", "wrk2", "pktgen", "slowhttptest", "iperf3"), required=True)
    parser.add_argument("--target", required=True, help="URL HTTP, host iPerf3 o destino IP de Pktgen")
    parser.add_argument("--connections", type=int, default=10, help="conexiones/streams concurrentes (máx. 1000)")
    parser.add_argument("--duration", type=int, default=30, help="duración en segundos (máx. 300)")
    parser.add_argument("--threads", type=int, default=2, help="hilos de Wrk/Wrk2 (máx. 32)")
    parser.add_argument("--rate", type=int, default=100, help="tasa objetivo: RPS, conexiones/s, PPS o Kbit/s según herramienta")
    parser.add_argument("-X", "--method", default="GET", help="verbo de SlowHTTPTest")
    parser.add_argument("-H", "--header", action="append", default=[], help="cabecera para Wrk/Wrk2; se puede repetir")
    parser.add_argument("--protocol", choices=("tcp", "udp"), default="tcp", help="protocolo de iPerf3")
    parser.add_argument("--interface", default="", help="interfaz de red para Pktgen, p. ej. eth0")
    parser.add_argument("--destination", default="", help="destino IP alternativo para Pktgen")
    parser.add_argument("--port", type=int, default=9000, help="puerto UDP de Pktgen")
    parser.add_argument("--packet-size", type=int, default=512, help="tamaño de paquete Pktgen (máx. 1200 bytes)")
    parser.add_argument("--count", type=int, default=1000, help="paquetes Pktgen")
    parser.add_argument("-o", "--output", default="", help="guardar plan JSON o script Pktgen")
    parser.add_argument("--execute", action="store_true", help="ejecutar el motor externo; omitido por defecto")
    parser.add_argument("--authorized", action="store_true", help="confirma que el destino está autorizado")
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


def _run_tools() -> int:
    print(json.dumps(tool_status(), indent=2, ensure_ascii=False))
    return 0


def _run_external(argv: list[str]) -> int:
    args = build_external_parser().parse_args(argv)
    config = ExternalConfig(
        tool=args.tool,
        target=args.target,
        connections=args.connections,
        duration=args.duration,
        threads=args.threads,
        rate=args.rate,
        method=args.method.upper(),
        headers=tuple(args.header),
        protocol=args.protocol,
        interface=args.interface,
        destination=args.destination,
        port=args.port,
        packet_size=args.packet_size,
        count=args.count,
        output=args.output,
    )
    try:
        if args.execute:
            result = execute(config, authorized=args.authorized)
            if hasattr(result, "stdout"):
                print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, file=sys.stderr, end="")
                return result.returncode
            print(f"Script Pktgen escrito en {result}")
            return 0
        rendered = plan_json(config)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
            print(f"Plan escrito en {output}")
        else:
            print(rendered)
        print("Modo simulación: no se ejecutó tráfico. Usa --execute --authorized para ejecutar.", file=sys.stderr)
        return 0
    except ExternalToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


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
    if argv and argv[0] == "tools":
        return _run_tools()
    if argv and argv[0] == "external":
        return _run_external(argv[1:])
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
