"""Motor de pruebas de carga HTTP basado únicamente en la biblioteca estándar."""
from __future__ import annotations

import asyncio
import json
import ssl
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

    def to_dict(self) -> dict:
        return asdict(self)


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
    request = Request(
        config.url,
        data=config.body,
        headers=config.headers,
        method=config.method.upper(),
    )
    context = None
    if not config.verify_tls and urlparse(config.url).scheme == "https":
        context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=config.timeout, context=context) as response:
            response.read()
            status = response.status
            return RequestResult(
                latency_ms=(time.perf_counter() - started) * 1000,
                status=status,
                ok=200 <= status < 400,
            )
    except HTTPError as exc:
        return RequestResult(
            latency_ms=(time.perf_counter() - started) * 1000,
            status=exc.code,
            ok=False,
            error=f"HTTP {exc.code}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return RequestResult(
            latency_ms=(time.perf_counter() - started) * 1000,
            status=None,
            ok=False,
            error=type(exc).__name__,
        )


async def run_load(config: LoadConfig, on_progress: Callable[[RequestResult], None] | None = None) -> LoadReport:
    """Ejecuta usuarios concurrentes durante ``duration`` segundos."""
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
        while time.perf_counter() < deadline:
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
    )


def report_json(report: LoadReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
