"""Modelos y validación de configuración compartidos por la GUI."""
from __future__ import annotations

from dataclasses import dataclass
import json

from .runner import LoadConfig
from .scenarios import ScenarioConfig


@dataclass(frozen=True)
class GuiValues:
    url: str
    method: str
    users: str
    duration: str
    ramp_up: str
    timeout: str
    headers: str
    body: str
    verify_tls: bool
    report_path: str
    chart_dir: str
    chart_format: str
    auto_charts: bool
    scenario: str = "Carga estándar"
    scenario_params: str = "{}"
    target_type: str = "Auto"
    throughput_method: str = "POST"
    throughput_payload_size: str = "262144"
    pps_host: str = "127.0.0.1"
    pps_port: str = "9000"
    pps_rate: str = "100"
    pps_packet_size: str = "512"


def parse_headers_text(value: str) -> dict[str, str]:
    """Convierte líneas ``Nombre: valor`` en un diccionario de cabeceras."""
    headers: dict[str, str] = {}
    for line_number, line in enumerate(value.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"Cabecera inválida en la línea {line_number}: usa Nombre: valor")
        name, content = line.split(":", 1)
        if not name.strip() or not content.strip():
            raise ValueError(f"Cabecera vacía en la línea {line_number}")
        headers[name.strip()] = content.strip()
    return headers


def config_from_values(values: GuiValues) -> LoadConfig:
    """Valida los campos de la GUI y crea la configuración del runner."""
    url = values.url.strip()
    if not url:
        raise ValueError("La URL es obligatoria")
    try:
        users = int(values.users)
        duration = float(values.duration)
        ramp_up = float(values.ramp_up)
        timeout = float(values.timeout)
    except ValueError as exc:
        raise ValueError("Usuarios, duración, rampa y timeout deben ser numéricos") from exc
    if users < 1:
        raise ValueError("Usuarios debe ser mayor que cero")
    if duration <= 0 or ramp_up < 0 or timeout <= 0:
        raise ValueError("Duración y timeout deben ser mayores que cero; la rampa no puede ser negativa")
    body = values.body.encode("utf-8") if values.body else None
    parsed_headers = parse_headers_text(values.headers) if values.headers.strip() else {}
    preset = {}
    if values.target_type and values.target_type != "Auto":
        from .runner import target_header_preset
        preset = target_header_preset(values.target_type)
    return LoadConfig(
        url=url,
        users=users,
        duration=duration,
        ramp_up=ramp_up,
        method=values.method.upper(),
        headers={**preset, **parsed_headers},
        body=body,
        timeout=timeout,
        verify_tls=values.verify_tls,
    )


def scenario_from_values(values: GuiValues) -> ScenarioConfig:
    """Convierte los campos de la interfaz en un escenario ejecutable."""
    try:
        params = json.loads(values.scenario_params or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Parámetros del escenario no son JSON válido: {exc.msg}") from exc
    if not isinstance(params, dict):
        raise ValueError("Los parámetros del escenario deben ser un objeto JSON")
    if values.scenario == "Throughput Stress":
        method = values.throughput_method.upper()
        if method not in {"POST", "PUT", "PATCH"}:
            raise ValueError("El método de Throughput Stress debe ser POST, PUT o PATCH")
        params.update(method=method, payload_size=_positive_integer(values.throughput_payload_size, "Tamaño del payload", 16 * 1024 * 1024))
    elif values.scenario == "PPS Stress":
        host = values.pps_host.strip()
        if not host:
            raise ValueError("El destino UDP es obligatorio")
        params.update(
            host=host,
            port=_positive_integer(values.pps_port, "Puerto", 65535),
            pps=_positive_integer(values.pps_rate, "PPS", 1000),
            packet_size=_positive_integer(values.pps_packet_size, "Tamaño del datagrama", 1200),
        )
    return ScenarioConfig(base=config_from_values(values), mode=values.scenario, params=params)


def _positive_integer(value: str, label: str, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} debe ser un número entero") from exc
    if parsed < 1 or (maximum is not None and parsed > maximum):
        limit = f" entre 1 y {maximum}" if maximum is not None else " mayor que cero"
        raise ValueError(f"{label} debe ser{limit}")
    return parsed
