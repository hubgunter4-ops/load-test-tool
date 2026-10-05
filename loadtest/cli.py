from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .runner import LoadConfig, LoadReport, report_json, run_load


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
    return parser


def _print_report(report: LoadReport) -> None:
    print(f"\nPrueba completada: {report.method} {report.url}")
    print(f"Usuarios: {report.users} | Duración: {report.duration_seconds:.2f}s | Peticiones: {report.total_requests}")
    print(f"RPS: {report.requests_per_second:.2f} | Éxito: {report.successful_requests} | Errores: {report.failed_requests} ({report.error_rate_percent:.2f}%)")
    latency = report.latency_ms
    print("Latencia (ms): " + " | ".join(f"{key}={value}" for key, value in latency.items()))
    print("Códigos HTTP: " + (", ".join(f"{key}: {value}" for key, value in report.status_codes.items()) or "ninguno"))
    if report.errors:
        print("Errores: " + ", ".join(f"{key}: {value}" for key, value in report.errors.items()))


def main(argv: list[str] | None = None) -> int:
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
        report = asyncio.run(run_load(config))
    except (OSError, ValueError, argparse.ArgumentTypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    if args.output:
        args.output.write_text(report_json(report) + "\n", encoding="utf-8")
        print(f"Informe JSON: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
