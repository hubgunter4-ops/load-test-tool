"""Orquestación de escenarios de estrés sobre el runner HTTP existente."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from .runner import LoadConfig, LoadReport, RequestResult, run_load

SCENARIO_MODES = (
    "Carga estándar",
    "Spike Test",
    "Stress Test progresivo",
    "Soak Test",
    "Escenarios mixtos",
    "Recuperación y fallos",
    "Prueba específica de base de datos",
    "Payload y rate limiting",
)

ProgressCallback = Callable[[RequestResult], None]
PhaseCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class ScenarioConfig:
    base: LoadConfig
    mode: str = "Carga estándar"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioReport:
    scenario: str
    aggregate: LoadReport
    phases: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        data = self.aggregate.to_dict()
        data["scenario"] = self.scenario
        data["phases"] = self.phases
        return data


def _number(params: dict[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"El parámetro {key} debe ser numérico") from exc


def _positive_int(params: dict[str, Any], key: str, default: int) -> int:
    value = int(_number(params, key, default))
    if value < 1:
        raise ValueError(f"El parámetro {key} debe ser mayor que cero")
    return value


def _phase_config(base: LoadConfig, users: int, duration: float, *, url: str | None = None, body: bytes | None = None) -> LoadConfig:
    return LoadConfig(
        url=url or base.url,
        users=users,
        duration=max(duration, 0.05),
        ramp_up=min(base.ramp_up, max(duration, 0.05)),
        method=base.method,
        headers=base.headers,
        body=base.body if body is None else body,
        timeout=base.timeout,
        verify_tls=base.verify_tls,
    )


def _safe_failure_url(params: dict[str, Any]) -> str:
    """URL de fallo controlado; nunca apunta a un host externo por defecto."""
    value = str(params.get("failure_url", "http://127.0.0.1:1/forced-failure"))
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("failure_url debe ser una URL HTTP o HTTPS válida")
    return value


def _payload(base: LoadConfig, params: dict[str, Any]) -> bytes | None:
    size = int(_number(params, "payload_size", 0))
    if size < 0:
        raise ValueError("payload_size no puede ser negativo")
    if size == 0:
        return base.body
    seed = str(params.get("payload_seed", "loadtest" )).encode("utf-8") or b"x"
    return (seed * ((size // len(seed)) + 1))[:size]


def _weighted_mix(base: LoadConfig, params: dict[str, Any]) -> list[tuple[LoadConfig, int]]:
    raw = params.get("mix")
    if not isinstance(raw, list) or not raw:
        raise ValueError("mix debe ser una lista de endpoints con url y weight")
    entries: list[tuple[LoadConfig, int]] = []
    total_weight = 0
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not item.get("url"):
            raise ValueError(f"mix[{index}] debe incluir url")
        weight = int(item.get("weight", 1))
        if weight < 1:
            raise ValueError(f"mix[{index}].weight debe ser mayor que cero")
        headers = dict(base.headers)
        headers.update({str(k): str(v) for k, v in (item.get("headers") or {}).items()})
        body = item.get("body")
        body_bytes = body.encode("utf-8") if isinstance(body, str) else base.body
        entries.append((LoadConfig(
            url=str(item["url"]), users=1, duration=base.duration, ramp_up=0,
            method=str(item.get("method", base.method)).upper(), headers=headers,
            body=body_bytes, timeout=base.timeout, verify_tls=base.verify_tls,
        ), weight))
        total_weight += weight
    return [(config, max(1, round(base.users * weight / total_weight))) for config, weight in entries]


def _aggregate(reports: list[LoadReport], base: LoadConfig, scenario: str, elapsed_override: float | None = None) -> ScenarioReport:
    total_requests = sum(report.total_requests for report in reports)
    successful = sum(report.successful_requests for report in reports)
    elapsed = elapsed_override if elapsed_override is not None else sum(report.duration_seconds for report in reports)
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}
    for report in reports:
        for key, value in report.status_codes.items():
            statuses[key] = statuses.get(key, 0) + value
        for key, value in report.errors.items():
            errors[key] = errors.get(key, 0) + value
        # El informe agregado mantiene los percentiles de fase como aproximación
        # cuando no se conservan todas las muestras individuales.
        for key in ("min", "avg", "p50", "p95", "p99", "max"):
            value = report.latency_ms.get(key)
            if value is not None:
                latencies.append(float(value))
    elapsed = max(elapsed, 0.001)
    latency = {
        "min": min(latencies) if latencies else None,
        "avg": sum(latencies) / len(latencies) if latencies else None,
        "p50": _percentile(latencies, 50),
        "p95": _percentile(latencies, 95),
        "p99": _percentile(latencies, 99),
        "max": max(latencies) if latencies else None,
    }
    aggregate = LoadReport(
        url=base.url, method=base.method.upper(), users=base.users,
        duration_seconds=round(elapsed, 3), total_requests=total_requests,
        successful_requests=successful, failed_requests=total_requests - successful,
        requests_per_second=round(total_requests / elapsed, 2),
        error_rate_percent=round((total_requests - successful) * 100 / total_requests, 2) if total_requests else 0.0,
        latency_ms={key: round(value, 2) if value is not None else None for key, value in latency.items()},
        status_codes=dict(sorted(statuses.items())), errors=dict(sorted(errors.items())),
    )
    return ScenarioReport(scenario=scenario, aggregate=aggregate, phases=[_phase_dict(report, index + 1) for index, report in enumerate(reports)])


def _phase_dict(report: LoadReport, index: int) -> dict[str, Any]:
    return {"phase": index, **report.to_dict()}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * p / 100
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


async def run_scenario(
    scenario: ScenarioConfig,
    on_progress: ProgressCallback | None = None,
    on_phase: PhaseCallback | None = None,
    stop_event=None,
) -> ScenarioReport:
    """Ejecuta el escenario solicitado y devuelve un informe agregado por fases."""
    base = scenario.base
    mode = scenario.mode
    params = scenario.params
    if mode not in SCENARIO_MODES:
        raise ValueError(f"Escenario no soportado: {mode}")
    total_duration = base.duration
    phases: list[LoadConfig] = []

    if mode == "Carga estándar":
        phases = [base]
    elif mode == "Spike Test":
        peak = _positive_int(params, "peak_users", max(base.users * 5, base.users + 1))
        first = total_duration * 0.25
        middle = total_duration * 0.5
        phases = [_phase_config(base, base.users, first), _phase_config(base, peak, middle), _phase_config(base, base.users, total_duration - first - middle)]
    elif mode == "Stress Test progresivo":
        raw_stages = params.get("stages", [base.users, base.users * 2, base.users * 4, base.users * 8])
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ValueError("stages debe ser una lista de cantidades de usuarios")
        stages = [_positive_int({"value": value}, "value", base.users) for value in raw_stages]
        each = total_duration / len(stages)
        phases = [_phase_config(base, users, each) for users in stages]
    elif mode == "Soak Test":
        phases = [_phase_config(base, base.users, total_duration)]
    elif mode == "Escenarios mixtos":
        weighted = _weighted_mix(base, params)
        # Cada endpoint recibe su proporción de usuarios durante la misma ventana.
        async def run_mix() -> list[LoadReport]:
            if on_phase:
                on_phase("Tráfico mixto ponderado", 1, len(weighted))
            return await asyncio.gather(*[run_load(_phase_config(config, users, total_duration), on_progress, stop_event) for config, users in weighted])
        reports = await run_mix()
        return _aggregate(list(reports), base, mode, elapsed_override=max((report.duration_seconds for report in reports), default=0.001))
    elif mode == "Recuperación y fallos":
        failure_url = _safe_failure_url(params)
        first = total_duration * 0.4
        failure = total_duration * 0.2
        phases = [_phase_config(base, base.users, first), _phase_config(base, base.users, failure, url=failure_url), _phase_config(base, base.users, total_duration - first - failure)]
    elif mode == "Prueba específica de base de datos":
        phases = [_phase_config(base, base.users, total_duration)]
    elif mode == "Payload y rate limiting":
        phases = [_phase_config(base, base.users, total_duration, body=_payload(base, params))]

    reports: list[LoadReport] = []
    for index, phase in enumerate(phases, start=1):
        if stop_event and stop_event.is_set():
            break
        if on_phase:
            labels = {
                "Spike Test": ("Baseline" if index == 1 else "Pico" if index == 2 else "Recuperación"),
                "Stress Test progresivo": f"Nivel {index}/{len(phases)} · {phase.users} usuarios",
                "Recuperación y fallos": ("Normal" if index != 2 else "Fallo controlado"),
                "Prueba específica de base de datos": f"Consulta: {params.get('query_type', 'endpoint configurado')}",
                "Payload y rate limiting": f"Payload: {len(phase.body or b'')} bytes · {params.get('rate_limit', 'sin límite explícito')}",
            }
            on_phase(labels.get(mode, f"Fase {index}/{len(phases)}"), index, len(phases))
        reports.append(await run_load(phase, on_progress, stop_event))
    return _aggregate(reports or [LoadReport(base.url, base.method, base.users, 0.001, 0, 0, 0, 0.0, 0.0, {"min": None, "avg": None, "p50": None, "p95": None, "p99": None, "max": None}, {}, {})], base, mode)


def scenario_json(report: ScenarioReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
