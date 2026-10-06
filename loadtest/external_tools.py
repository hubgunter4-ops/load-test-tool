"""Adaptadores opcionales para generadores externos de carga.

Los comandos se construyen sin ejecutarse por defecto. La ejecución real requiere
``--execute --authorized`` en la CLI y límites conservadores para evitar lanzar
carga accidentalmente contra destinos no autorizados.
"""
from __future__ import annotations

import json
import ipaddress
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TOOL_NAMES = ("wrk", "wrk2", "pktgen", "slowhttptest", "iperf3")
EXECUTION_ENGINES = ("Integrado", "Wrk", "Wrk2", "Pktgen", "SlowHTTPTest", "iPerf3")
ENGINE_TO_TOOL = {
    "Wrk": "wrk",
    "Wrk2": "wrk2",
    "Pktgen": "pktgen",
    "SlowHTTPTest": "slowhttptest",
    "iPerf3": "iperf3",
}


@dataclass(frozen=True)
class ExternalConfig:
    tool: str
    target: str
    connections: int = 10
    duration: int = 30
    threads: int = 2
    rate: int = 100
    method: str = "GET"
    headers: tuple[str, ...] = ()
    protocol: str = "tcp"
    interface: str = ""
    destination: str = ""
    port: int = 9000
    packet_size: int = 512
    count: int = 1000
    output: str = ""


class ExternalToolError(ValueError):
    """Error de validación o disponibilidad de una herramienta externa."""


def tool_path(tool: str) -> str | None:
    """Devuelve la ruta del ejecutable o el estado especial de pktgen."""
    normalized = tool.lower()
    if normalized == "pktgen":
        return "/proc/net/pktgen" if os.path.isdir("/proc/net/pktgen") else None
    if normalized not in TOOL_NAMES:
        raise ExternalToolError(f"Herramienta no soportada: {tool}")
    return shutil.which(normalized)


def tool_status() -> list[dict[str, Any]]:
    """Lista disponibilidad local sin instalar ni ejecutar herramientas."""
    status = []
    for tool in TOOL_NAMES:
        path = tool_path(tool)
        status.append({
            "tool": tool,
            "available": path is not None,
            "path": path,
            "mode": "kernel/procfs" if tool == "pktgen" else "executable",
        })
    return status


def _positive(name: str, value: int, maximum: int) -> int:
    if value < 1 or value > maximum:
        raise ExternalToolError(f"{name} debe estar entre 1 y {maximum}")
    return value


def validate_config(config: ExternalConfig) -> None:
    if config.tool not in TOOL_NAMES:
        raise ExternalToolError(f"Herramienta no soportada: {config.tool}")
    if not config.target.strip():
        raise ExternalToolError("target es obligatorio")
    _positive("connections", config.connections, 1000)
    _positive("duration", config.duration, 300)
    _positive("threads", config.threads, 32)
    rate_limit = 1_000_000 if config.tool == "iperf3" else 1000
    _positive("rate", config.rate, rate_limit)
    _positive("port", config.port, 65535)
    _positive("packet_size", config.packet_size, 1200)
    _positive("count", config.count, 1_000_000)
    if config.protocol not in {"tcp", "udp"}:
        raise ExternalToolError("protocol debe ser tcp o udp")
    if config.tool == "pktgen" and not config.interface:
        raise ExternalToolError("pktgen requiere --interface")
    if config.tool == "pktgen":
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", config.interface):
            raise ExternalToolError("interface de Pktgen contiene caracteres no permitidos")
        destination = config.destination or config.target
        try:
            ipaddress.ip_address(destination)
        except ValueError as exc:
            raise ExternalToolError("Pktgen requiere un destino IPv4 o IPv6 válido") from exc
    if config.tool == "slowhttptest" and not config.target.startswith(("http://", "https://")):
        raise ExternalToolError("slowhttptest requiere una URL HTTP o HTTPS")


def _duration(value: int) -> str:
    return f"{value}s"


def build_command(config: ExternalConfig, *, require_available: bool = True) -> list[str]:
    """Construye el argv de wrk/wrk2/slowhttptest/iperf3."""
    validate_config(config)
    binary = tool_path(config.tool)
    if config.tool == "pktgen":
        raise ExternalToolError("pktgen se configura como script; usa build_pktgen_script")
    if binary is None and require_available:
        raise ExternalToolError(f"No se encontró el ejecutable: {config.tool}")
    binary = binary or config.tool

    if config.tool in {"wrk", "wrk2"}:
        command = [binary, f"-t{config.threads}", f"-c{config.connections}", f"-d{_duration(config.duration)}", "--latency"]
        if config.tool == "wrk2":
            command.append(f"-R{config.rate}")
        for header in config.headers:
            command.extend(("-H", header))
        command.append(config.target)
        return command

    if config.tool == "slowhttptest":
        return [
            binary, "-H", "-g", "-c", str(config.connections), "-l", str(config.duration),
            "-r", str(min(config.rate, 100)), "-t", config.method, "-u", config.target,
            "-i", "10", "-p", "3",
        ]

    command = [binary, "-c", config.target, "-t", str(config.duration), "-P", str(config.connections), "-J"]
    if config.protocol == "udp":
        command.extend(("-u", "-b", f"{config.rate}K"))
    return command


def build_pktgen_script(config: ExternalConfig) -> str:
    """Genera un script de configuración de Linux kernel pktgen sin ejecutarlo."""
    validate_config(config)
    destination = config.destination or config.target
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "# Generado por load-test-tool; ejecutar solo en un laboratorio autorizado.",
        f"INTERFACE={shlex.quote(config.interface)}",
        f"DESTINATION={shlex.quote(destination)}",
        'PGDEV="/proc/net/pktgen/${INTERFACE}"',
        'echo "Configurando $PGDEV"',
        'echo reset > /proc/net/pktgen/pgctrl',
        'printf "add_device %s\\n" "$INTERFACE" > /proc/net/pktgen/kpktgend_0',
        f'printf "count %s\\n" "{config.count}" > "$PGDEV"',
        f'printf "pkt_size %s\\n" "{config.packet_size}" > "$PGDEV"',
        f'printf "ratep %s\\n" "{config.rate}" > "$PGDEV"',
        'printf "dst_min %s\\n" "$DESTINATION" > "$PGDEV"',
        f'printf "udp_dst_min %s\\n" "{config.port}" > "$PGDEV"',
        'echo start > /proc/net/pktgen/pgctrl',
        'cat "$PGDEV"',
    ]
    return "\n".join(lines) + "\n"


def plan(config: ExternalConfig) -> dict[str, Any]:
    """Devuelve un plan serializable para revisión o auditoría."""
    validate_config(config)
    result: dict[str, Any] = {"config": asdict(config), "available": tool_path(config.tool) is not None}
    if config.tool == "pktgen":
        result["script"] = build_pktgen_script(config)
    else:
        result["command"] = build_command(config, require_available=False)
    return result


def execute(config: ExternalConfig, *, authorized: bool = False) -> subprocess.CompletedProcess[str] | Path:
    """Ejecuta con autorización explícita; pktgen solo escribe su script."""
    if not authorized:
        raise ExternalToolError("La ejecución requiere autorización explícita (--authorized)")
    if config.tool != "pktgen" and tool_path(config.tool) is None:
        raise ExternalToolError(f"No se encontró el ejecutable: {config.tool}")
    result = plan(config)
    if config.tool == "pktgen":
        output = Path(config.output or f"pktgen-{config.interface}.sh")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(result["script"], encoding="utf-8")
        output.chmod(0o750)
        return output
    try:
        return subprocess.run(result["command"], check=False, text=True, capture_output=True, timeout=config.duration + 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExternalToolError(f"{config.tool} no pudo ejecutarse o superó el timeout") from exc


def plan_json(config: ExternalConfig) -> str:
    return json.dumps(plan(config), indent=2, ensure_ascii=False)
