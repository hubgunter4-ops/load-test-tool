"""Modelos y validación de configuración compartidos por la GUI."""
from __future__ import annotations

from dataclasses import dataclass

from .runner import LoadConfig


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
    return LoadConfig(
        url=url,
        users=users,
        duration=duration,
        ramp_up=ramp_up,
        method=values.method.upper(),
        headers=parse_headers_text(values.headers),
        body=body,
        timeout=timeout,
        verify_tls=values.verify_tls,
    )
