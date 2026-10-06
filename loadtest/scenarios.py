"""Orquestación de escenarios de estrés sobre el runner HTTP existente."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
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
    "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)",
    "Throughput Stress",
    "PPS Stress",
    "Pruebas de Conmutación por Error (Failover)",
    "Estrés de Auto-escalado",
)

SCENARIO_PARAMS_EXAMPLES = {
    "Carga estándar": "{}",
    "Spike Test": '{\n  "peak_users": 100\n}',
    "Stress Test progresivo": '{\n  "stages": [10, 25, 50, 100]\n}',
    "Soak Test": "{}",
    "Escenarios mixtos": '{\n  "mix": [{"url": "http://localhost:8000/health", "weight": 1}]\n}',
    "Recuperación y fallos": '{\n  "failure_url": "http://127.0.0.1:1/forced-failure"\n}',
    "Prueba específica de base de datos": '{\n  "query_type": "lectura_simple"\n}',
    "Payload y rate limiting": '{\n  "payload_size": 4096,\n  "rate_limit": 50\n}',
    "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)": '{\n  "methods": ["GET", "HEAD"],\n  "connection_close": true\n}',
    "Throughput Stress": '{\n  "method": "POST",\n  "payload_size": 262144\n}',
    "PPS Stress": '{\n  "host": "127.0.0.1",\n  "port": 9000,\n  "pps": 100,\n  "packet_size": 512\n}',
    "Pruebas de Conmutación por Error (Failover)": '{\n  "primary_url": "http://localhost:8000/health",\n  "fallback_url": "http://127.0.0.1:1/fallback",\n  "switch_after": 15\n}',
    "Estrés de Auto-escalado": '{\n  "min_users": 10,\n  "max_users": 100,\n  "steps": 4\n}',
}

ProgressCallback = Callable[[RequestResult], None]
PhaseCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class ScenarioConfig:
    base: LoadConfig
    mode: str = "Carga estándar"
    params: dict[str, Any] = field(default_factory=dict)
    iterations: int = 1
    loop_delay: float = 0.0


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


def _phase_config(
    base: LoadConfig,
    users: int,
    duration: float,
    *,
    url: str | None = None,
    body: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> LoadConfig:
    return LoadConfig(
        url=url or base.url,
        users=users,
        duration=max(duration, 0.05),
        ramp_up=min(base.ramp_up, max(duration, 0.05)),
        method=method or base.method,
        headers=base.headers if headers is None else headers,
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


async def _resolve_udp_destination(host: str, port: int):
    try:
        destinations = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_DGRAM
        )
    except OSError as exc:
        raise ValueError(f"no se pudo resolver el destino UDP {host!r}: {exc}") from exc
    for family, _socktype, _protocol, _canonname, sockaddr in destinations:
        address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        if not (address.is_unspecified or address.is_multicast or address.is_reserved):
            return address, family, sockaddr
    raise ValueError("host no resuelve a una dirección unicast válida")


async def _run_pps_stress(
    base: LoadConfig,
    params: dict[str, Any],
    on_progress: ProgressCallback | None,
    stop_event,
) -> LoadReport:
    host = str(params.get("host", "127.0.0.1")).strip()
    if not host:
        raise ValueError("host es obligatorio")
    port = _positive_int(params, "port", 9000)
    if port > 65535:
        raise ValueError("port debe estar entre 1 y 65535")
    pps = _positive_int(params, "pps", 100)
    if pps > 1000:
        raise ValueError("pps no puede superar el límite de 1000 datagramas/s")
    packet_size = _positive_int(params, "packet_size", 512)
    if packet_size > 1200:
        raise ValueError("packet_size no puede superar 1200 bytes")
    duration = base.duration
    if duration > 60:
        raise ValueError("PPS Stress limita cada ejecución a 60 segundos")
        if duration <= 0:
            raise ValueError("duration debe ser mayor que cero")

    address, family, destination = await _resolve_udp_destination(host, port)

    seed = str(params.get("payload_seed", "loadtest")).encode("utf-8") or b"x"
    payload = (seed * ((packet_size // len(seed)) + 1))[:packet_size]
    udp_socket = socket.socket(family, socket.SOCK_DGRAM)
    udp_socket.setblocking(False)
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    interval = 1 / pps
    next_send = loop.time()
    last_progress = next_send
    pending_count = 0
    sent_count = 0
    failed_count = 0
    errors: dict[str, int] = {}

    def publish_progress(now: float) -> None:
        nonlocal pending_count, last_progress
        if on_progress and pending_count and (now - last_progress >= 0.1 or now >= deadline):
            on_progress(RequestResult(
                latency_ms=0.0,
                status=None,
                ok=True,
                bytes_sent=pending_count * packet_size,
                request_count=pending_count,
            ))
            pending_count = 0
            last_progress = now

    try:
        while loop.time() < deadline and not (stop_event and stop_event.is_set()):
            now = loop.time()
            if now < next_send:
                await asyncio.sleep(min(next_send - now, deadline - now))
                continue
            try:
                udp_socket.sendto(payload, destination)
                sent_count += 1
                pending_count += 1
            except OSError as exc:
                failed_count += 1
                error_name = type(exc).__name__
                errors[error_name] = errors.get(error_name, 0) + 1
            next_send += interval
            current = loop.time()
            if next_send < current:
                next_send = current + interval
            publish_progress(current)
    finally:
        udp_socket.close()

    finished = time.perf_counter()
    elapsed = max(finished - started, 0.001)
    publish_progress(loop.time())
    total = sent_count + failed_count
    return LoadReport(
        url=f"udp://{address}:{port}",
        method="UDP",
        users=1,
        duration_seconds=round(elapsed, 3),
        total_requests=total,
        successful_requests=sent_count,
        failed_requests=failed_count,
        requests_per_second=round(sent_count / elapsed, 2),
        error_rate_percent=round(failed_count * 100 / total, 2) if total else 0.0,
        latency_ms={key: None for key in ("min", "avg", "p50", "p95", "p99", "max")},
        status_codes={},
        errors=dict(sorted(errors.items())),
        bytes_sent=sent_count * packet_size,
        upload_mbps=round(sent_count * packet_size * 8 / elapsed / 1_000_000, 4),
        packets_per_second=round(sent_count / elapsed, 2),
        datagrams_sent=sent_count,
    )


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
    bytes_sent = sum(report.bytes_sent for report in reports)
    bytes_received = sum(report.bytes_received for report in reports)
    datagrams_sent = sum(report.datagrams_sent for report in reports)
    methods = {report.method for report in reports}
    latency = {
        "min": min(latencies) if latencies else None,
        "avg": sum(latencies) / len(latencies) if latencies else None,
        "p50": _percentile(latencies, 50),
        "p95": _percentile(latencies, 95),
        "p99": _percentile(latencies, 99),
        "max": max(latencies) if latencies else None,
    }
    aggregate = LoadReport(
        url=base.url, method=next(iter(methods)) if len(methods) == 1 else base.method.upper(), users=base.users,
        duration_seconds=round(elapsed, 3), total_requests=total_requests,
        successful_requests=successful, failed_requests=total_requests - successful,
        requests_per_second=round(total_requests / elapsed, 2),
        error_rate_percent=round((total_requests - successful) * 100 / total_requests, 2) if total_requests else 0.0,
        latency_ms={key: round(value, 2) if value is not None else None for key, value in latency.items()},
        status_codes=dict(sorted(statuses.items())), errors=dict(sorted(errors.items())),
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        upload_mbps=round(bytes_sent * 8 / elapsed / 1_000_000, 4),
        download_mbps=round(bytes_received * 8 / elapsed / 1_000_000, 4),
        packets_per_second=round(datagrams_sent / elapsed, 2),
        datagrams_sent=datagrams_sent,
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
    if scenario.iterations < 1 or scenario.iterations > 100:
        raise ValueError("iterations debe estar entre 1 y 100")
    if scenario.loop_delay < 0 or scenario.loop_delay > 3600:
        raise ValueError("loop_delay debe estar entre 0 y 3600 segundos")
    if scenario.iterations > 1:
        return await run_scenario_loop(scenario, on_progress, on_phase, stop_event)
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
    elif mode == "Throughput Stress":
        method = str(params.get("method", base.method)).strip().upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("method no válido para Throughput Stress")
        size = _positive_int(params, "payload_size", 262144)
        if size > 16 * 1024 * 1024:
            raise ValueError("payload_size no puede superar 16777216 bytes para Throughput Stress")
        phases = [_phase_config(base, base.users, total_duration, body=_payload(base, {**params, "payload_size": size}), method=method)]
    elif mode == "PPS Stress":
        if on_phase:
            on_phase(f"UDP · {params.get('pps', 100)} PPS solicitados", 1, 1)
        report = await _run_pps_stress(base, params, on_progress, stop_event)
        return ScenarioReport(scenario=mode, aggregate=report, phases=[_phase_dict(report, 1)])
    elif mode == "Pruebas de Conmutación por Error (Failover)":
        primary_url = str(params.get("primary_url", base.url)).strip()
        fallback_url = str(params.get("fallback_url", "http://127.0.0.1:1/fallback")).strip()
        for label, value in (("primary_url", primary_url), ("fallback_url", fallback_url)):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{label} debe ser una URL HTTP o HTTPS válida")
        switch_after = _number(params, "switch_after", total_duration / 2)
        if switch_after <= 0 or switch_after >= total_duration:
            raise ValueError("switch_after debe estar entre 0 y la duración total")
        phases = [
            _phase_config(base, base.users, switch_after, url=primary_url),
            _phase_config(base, base.users, total_duration - switch_after, url=fallback_url),
        ]
    elif mode == "Estrés de Auto-escalado":
        minimum = _positive_int(params, "min_users", base.users)
        maximum = _positive_int(params, "max_users", max(base.users * 4, minimum))
        steps = _positive_int(params, "steps", 4)
        if maximum < minimum:
            raise ValueError("max_users debe ser mayor o igual que min_users")
        if steps < 2 or steps > 12:
            raise ValueError("steps debe estar entre 2 y 12")
        ascending = [round(minimum + (maximum - minimum) * index / (steps - 1)) for index in range(steps)]
        stage_users = ascending + ascending[-2::-1]
        each = total_duration / len(stage_users)
        phases = [_phase_config(base, users, each) for users in stage_users]
    elif mode == "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)":
        raw_methods = params.get("methods", [base.method])
        if not isinstance(raw_methods, list) or not raw_methods:
            raise ValueError("methods debe ser una lista no vacía de métodos HTTP")
        allowed_methods = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
        methods = []
        for method in raw_methods:
            if not isinstance(method, str) or method.strip().upper() not in allowed_methods:
                raise ValueError("methods solo admite GET, HEAD, POST, PUT, PATCH y DELETE")
            methods.append(method.strip().upper())
        connection_close = params.get("connection_close", True)
        if not isinstance(connection_close, bool):
            raise ValueError("connection_close debe ser booleano")
        headers = dict(base.headers)
        if connection_close:
            headers = {key: value for key, value in headers.items() if key.lower() != "connection"}
            headers["Connection"] = "close"
        phase_duration = total_duration / len(methods)
        phases = [
            _phase_config(base, base.users, phase_duration, method=method, headers=headers)
            for method in methods
        ]

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
                "Estrés de Protocolo y Red (Capa de Aplicación y Transporte)": (
                    f"HTTP/1.1 {phase.method} · "
                    f"{'cierre por solicitud' if phase.headers.get('Connection', '').lower() == 'close' else 'conexión configurada'}"
                ),
                "Throughput Stress": f"HTTP {phase.method} · {len(phase.body or b'')} bytes por petición",
                "Pruebas de Conmutación por Error (Failover)": (
                    "Primario · tráfico normal" if index == 1 else "Fallback · conmutación programada"
                ),
                "Estrés de Auto-escalado": f"Nivel {index}/{len(phases)} · {phase.users} usuarios",
            }
            on_phase(labels.get(mode, f"Fase {index}/{len(phases)}"), index, len(phases))
        reports.append(await run_load(phase, on_progress, stop_event))
    return _aggregate(reports or [LoadReport(base.url, base.method, base.users, 0.001, 0, 0, 0, 0.0, 0.0, {"min": None, "avg": None, "p50": None, "p95": None, "p99": None, "max": None}, {}, {})], base, mode)


async def run_scenario_loop(
    scenario: ScenarioConfig,
    on_progress: ProgressCallback | None = None,
    on_phase: PhaseCallback | None = None,
    stop_event=None,
) -> ScenarioReport:
    """Repite una acción completa un número finito de veces y agrega sus fases."""
    reports: list[ScenarioReport] = []
    single = ScenarioConfig(scenario.base, scenario.mode, scenario.params, iterations=1, loop_delay=0.0)
    for iteration in range(1, scenario.iterations + 1):
        if stop_event and stop_event.is_set():
            break

        def phase_callback(name: str, index: int, total: int, current=iteration) -> None:
            if on_phase:
                on_phase(f"Bucle {current}/{scenario.iterations} · {name}", index, total)

        reports.append(await run_scenario(single, on_progress, phase_callback, stop_event))
        if iteration < scenario.iterations and scenario.loop_delay and not (stop_event and stop_event.is_set()):
            await asyncio.sleep(scenario.loop_delay)

    elapsed = sum(report.aggregate.duration_seconds for report in reports)
    elapsed += max(0, len(reports) - 1) * scenario.loop_delay
    aggregate = _aggregate(
        [report.aggregate for report in reports] or [
            LoadReport(scenario.base.url, scenario.base.method, scenario.base.users, 0.001, 0, 0, 0, 0.0, 0.0,
                       {"min": None, "avg": None, "p50": None, "p95": None, "p99": None, "max": None}, {}, {})
        ],
        scenario.base,
        f"{scenario.mode} · bucle x{scenario.iterations}",
        elapsed_override=max(elapsed, 0.001),
    )
    phases: list[dict[str, Any]] = []
    for iteration, report in enumerate(reports, start=1):
        for phase in report.phases:
            phases.append({"loop": iteration, **phase})
    return ScenarioReport(scenario=f"{scenario.mode} · bucle x{scenario.iterations}", aggregate=aggregate.aggregate, phases=phases)


def scenario_json(report: ScenarioReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
