# Load Test Tool

CLI autocontenido para **pruebas de carga HTTP concurrentes** y visualización de resultados, pensado para estresar endpoints de aplicaciones que consultan una base de datos y observar capacidad, errores y latencia.

> Úsalo únicamente contra sistemas propios o para los que tengas autorización. Empieza en staging y aumenta la carga gradualmente.

## Instalación

Para usar solo la prueba de carga:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Para habilitar la generación de gráficos:

```bash
pip install -e '.[charts]'
```

También se puede ejecutar sin instalarlo:

```bash
PYTHONPATH=. python3 -m loadtest.cli http://localhost:8000/health -u 20 -d 30 --ramp-up 10
```

## Ejecutar una prueba

```bash
loadtest https://staging.example.com/api/orders \
  --users 50 --duration 120 --ramp-up 30 \
  -H 'Authorization: Bearer TOKEN' \
  -H 'Accept: application/json' \
  --timeout 15 --output reports/orders.json
```

Para un endpoint de escritura:

```bash
loadtest http://localhost:8000/api/query -X POST \
  -H 'Content-Type: application/json' \
  --body '{"query":"SELECT 1"}' -u 10 -d 20 \
  --output reports/query.json
```

## Generar gráficos desde JSON

El subcomando `chart` lee el informe generado con `--output` y crea tres gráficos estáticos:

- `latency.png`: latencia mínima, promedio, p50, p95, p99 y máxima.
- `status_codes.png`: distribución de códigos HTTP, diferenciando respuestas exitosas y errores.
- `summary.png`: volumen de solicitudes e indicadores de RPS, error, usuarios y duración.

```bash
loadtest chart reports/orders.json --output-dir reports/orders-charts
```

Por defecto genera PNG. Para documentos o integración web puede generar SVG:

```bash
loadtest chart reports/orders.json \
  --output-dir reports/orders-charts \
  --format svg
```

Opciones del subcomando:

| Opción | Predeterminado | Descripción |
|---|---:|---|
| `report` | — | Archivo JSON de resultados |
| `--output-dir` | `charts` | Directorio de gráficos |
| `--format` | `png` | `png` o `svg` |
| `--dpi` | `160` | Resolución de los PNG |

El JSON actual contiene métricas agregadas, por lo que los gráficos muestran el resumen de la prueba. Para una serie temporal por intervalo sería necesario ampliar el informe para conservar muestras durante la ejecución.

## Métricas

- **RPS**: solicitudes completadas por segundo.
- **Latencia**: mínima, promedio, p50, p95, p99 y máxima, en milisegundos.
- **Códigos HTTP** y tasa de error.
- **Errores de red** agrupados por tipo.
- Informe JSON reproducible con `--output`.

Para evaluar la base de datos, prueba endpoints representativos (lecturas simples, lecturas con joins, escrituras y consultas pesadas) y correlaciona este informe con CPU, memoria, conexiones activas, locks, cache hit ratio y tiempos de consulta de la base de datos. La herramienta no accede directamente al motor de base de datos para evitar acoplarse a un proveedor específico.

## Parámetros principales

| Opción | Predeterminado | Descripción |
|---|---:|---|
| `url` | — | Endpoint HTTP/HTTPS obligatorio |
| `--users` | `10` | Usuarios concurrentes |
| `--duration` | `30` | Duración en segundos |
| `--ramp-up` | `0` | Tiempo para incorporar usuarios gradualmente |
| `--method` | `GET` | Método HTTP |
| `--header` | — | Cabecera `Nombre: valor`, repetible |
| `--body` / `--body-file` | — | Cuerpo de la petición |
| `--timeout` | `10` | Timeout por petición |
| `--insecure` | desactivado | Deshabilita verificación TLS; solo desarrollo |
| `--output` | — | Archivo JSON de resultados |

## Pruebas

```bash
python3 -m unittest discover -v
```

## Estado del repositorio

Versión `0.2.0`, con pruebas de carga, exportación JSON y gráficos PNG/SVG.
