"""Motor de pruebas de carga HTTP basado únicamente en la biblioteca estándar."""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LoadConfig:
    url: str
    users: int = 10
    duration: float = 30.0
    ramp_up: float = 0.0
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = 10.0
    verify_tls: bool = True


@dataclass
class RequestResult:
    latency_ms: float
    status: int | None
    ok: bool
    error: str | None = None
    bytes_sent: int = 0
    bytes_received: int = 0
    request_count: int = 1


@dataclass
class LoadReport:
    url: str
    method: str
    users: int
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    requests_per_second: float
    error_rate_percent: float
    latency_ms: dict[str, float | None]
    status_codes: dict[str, int]
    errors: dict[str, int]
    bytes_sent: int = 0
    bytes_received: int = 0
    upload_mbps: float = 0.0
    download_mbps: float = 0.0
    packets_per_second: float = 0.0
    datagrams_sent: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _looks_like_json(value: bytes | None) -> bool:
    if value is None:
        return False
    stripped = value.lstrip()
    if not stripped:
        return False
    try:
        json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True


TARGET_HEADER_PRESETS: dict[str, dict[str, str]] = {
    "Auto": {},
    "REST API": {"Accept": "application/json"},
    "GraphQL": {"Accept": "application/json", "Content-Type": "application/json"},
    "Health Check": {"Accept": "application/json"},
    "Upload": {"Accept": "application/json", "Content-Type": "application/octet-stream"},
    "Custom": {},
}


def detect_target_type(url: str, method: str, body: bytes | None = None) -> str:
    """Detecta el tipo de target con heurísticas simples sobre URL, método y payload."""
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    method_name = str(method or "GET").upper()
    host = (parsed.hostname or "").lower()

    if "/graphql" in path or "graphql" in (parsed.query or "").lower():
        return "GraphQL"
    if any(token in path for token in ("/api", "/query", "/search", ".json")):
        return "REST API"
    if "/health" in path or "/status" in path or host in {"localhost", "127.0.0.1", "::1"} and ("health" in path or "status" in path):
        return "Health Check"
    if method_name in {"POST", "PUT", "PATCH"} and body and len(body) > 1024:
        return "Upload"
    if _looks_like_json(body):
        return "REST API"
    return "Auto"


def target_header_preset(target_type: str | None) -> dict[str, str]:
    """Devuelve el preset de cabeceras para un tipo de target concreto."""
    normalized = (target_type or "Auto").strip()
    if normalized == "Auto":
        normalized = detect_target_type("", "GET")
    return dict(TARGET_HEADER_PRESETS.get(normalized, TARGET_HEADER_PRESETS["Auto"]))


def default_headers_for_target(
    url: str,
    method: str,
    body: bytes | None = None,
    configured_headers: dict[str, str] | None = None,
    target_type: str | None = None,
) -> dict[str, str]:
    """Devuelve las cabeceras por defecto acordes al destino y permite sobrescribirlas."""
    default_headers: dict[str, str] = {"User-Agent": "load-test-tool"}
    selected_target = target_type or detect_target_type(url, method, body)
    preset = target_header_preset(selected_target)
    default_headers.update(preset)

    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    method_name = str(method or "GET").upper()
    host = (parsed.hostname or "").lower()

    target_is_api = selected_target in {"REST API", "GraphQL"} or any(token in path for token in ("/api", "/graphql", "/query", "/search", ".json"))
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        target_is_api = target_is_api or "/health" in path or "/status" in path

    if "Accept" not in default_headers:
        if target_is_api or method_name in {"POST", "PUT", "PATCH", "DELETE"}:
            default_headers["Accept"] = "application/json"
        else:
            default_headers["Accept"] = "*/*"

    if "Content-Type" not in default_headers and method_name in {"POST", "PUT", "PATCH"}:
        if _looks_like_json(body):
            default_headers["Content-Type"] = "application/json"
        elif body is not None:
            default_headers["Content-Type"] = "text/plain; charset=utf-8"

    resolved = default_headers.copy()
    if configured_headers:
        resolved.update({str(key): str(value) for key, value in configured_headers.items()})
    return resolved


def percentile(values: Iterable[float], p: float) -> float | None:
    """Calcula un percentil lineal; devuelve None si no hay muestras."""
    ordered = sorted(values)
    if not ordered:
        return None
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def _request(config: LoadConfig) -> RequestResult:
    started = time.perf_counter()
    bytes_sent = len(config.body or b"")
    request_headers = default_headers_for_target(config.url, config.method, config.body, config.headers)
    request = Request(
        config.url,
        data=config.body,
        headers=request_headers,
        method=config.method.upper(),
    )
    context = None
    if not config.verify_tls and urlparse(config.url).scheme == "https":
        context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=config.timeout, context=context) as response:
            bytes_received = len(response.read())
            status = response.status
            return RequestResult(
                latency_ms=(time.perf_counter() - started) * 1000,
                status=status,
                ok=200 <= status < 400,
                bytes_sent=bytes_sent,
                bytes_received=bytes_received,
            )
    except HTTPError as exc:
        bytes_received = len(exc.read())
        return RequestResult(
            latency_ms=(time.perf_counter() - started) * 1000,
            status=exc.code,
            ok=False,
            error=f"HTTP {exc.code}",
            bytes_sent=bytes_sent,
            bytes_received=bytes_received,
        )
    except (URLError, TimeoutError, OSError) as exc:
        return RequestResult(
            latency_ms=(time.perf_counter() - started) * 1000,
            status=None,
            ok=False,
            error=type(exc).__name__,
            bytes_sent=bytes_sent,
        )


async def run_load(
    config: LoadConfig,
    on_progress: Callable[[RequestResult], None] | None = None,
    stop_event: threading.Event | None = None,
) -> LoadReport:
    """Ejecuta usuarios concurrentes durante ``duration`` segundos.

    ``stop_event`` permite cancelar cooperativamente una ejecución desde otra
    hebra, por ejemplo desde una interfaz gráfica.
    """
    if config.users < 1:
        raise ValueError("users debe ser mayor que cero")
    if config.duration <= 0:
        raise ValueError("duration debe ser mayor que cero")
    parsed = urlparse(config.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url debe ser una URL HTTP o HTTPS válida")

    started = time.perf_counter()
    deadline = started + config.duration
    results: list[RequestResult] = []
    lock = asyncio.Lock()

    async def worker(index: int) -> None:
        if config.ramp_up > 0 and config.users > 1:
            await asyncio.sleep(config.ramp_up * index / (config.users - 1))
        while time.perf_counter() < deadline and not (stop_event and stop_event.is_set()):
            result = await asyncio.to_thread(_request, config)
            async with lock:
                results.append(result)
            if on_progress:
                on_progress(result)

    await asyncio.gather(*(worker(i) for i in range(config.users)))
    elapsed = max(time.perf_counter() - started, 0.001)
    latencies = [r.latency_ms for r in results]
    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}
    for result in results:
        if result.status is not None:
            key = str(result.status)
            statuses[key] = statuses.get(key, 0) + 1
        if result.error:
            errors[result.error] = errors.get(result.error, 0) + 1
    total = len(results)
    successful = sum(1 for r in results if r.ok)
    bytes_sent = sum(result.bytes_sent for result in results)
    bytes_received = sum(result.bytes_received for result in results)
    return LoadReport(
        url=config.url,
        method=config.method.upper(),
        users=config.users,
        duration_seconds=round(elapsed, 3),
        total_requests=total,
        successful_requests=successful,
        failed_requests=total - successful,
        requests_per_second=round(total / elapsed, 2),
        error_rate_percent=round((total - successful) * 100 / total, 2) if total else 0.0,
        latency_ms={
            "min": round(min(latencies), 2) if latencies else None,
            "avg": round(mean(latencies), 2) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": round(max(latencies), 2) if latencies else None,
        },
        status_codes=dict(sorted(statuses.items())),
        errors=dict(sorted(errors.items())),
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        upload_mbps=round(bytes_sent * 8 / elapsed / 1_000_000, 4),
        download_mbps=round(bytes_received * 8 / elapsed / 1_000_000, 4),
    )


def report_json(report: LoadReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
