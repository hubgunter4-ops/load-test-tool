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

Tkinter normalmente viene incluido con Python. En distribuciones Linux que lo separan del intérprete puede ser necesario instalar el paquete del sistema `python3-tk`.

También se puede ejecutar sin instalarlo:

```bash
PYTHONPATH=. python3 -m loadtest.cli http://localhost:8000/health -u 20 -d 30 --ramp-up 10
```

## Interfaz gráfica Tkinter

Para ejecutar la herramienta desde una interfaz visual:

```bash
loadtest-gui
```

La ventana distribuye el flujo en cuatro áreas:

1. **Endpoint y concurrencia**: URL, método HTTP, usuarios, duración, rampa y timeout.
2. **Petición**: cabeceras por línea y cuerpo opcional para POST/PUT/PATCH.
3. **Salida**: archivo JSON, directorio de gráficos, formato PNG/SVG y verificación TLS.
4. **Ejecución e informe**: inicio/detención, métricas en vivo, registro de eventos e informe final.

La prueba se ejecuta en un hilo independiente para que la ventana no se congele. **Detener** activa una cancelación cooperativa y termina después de la petición que esté en curso. Al finalizar se guarda el JSON y, si está seleccionado, se generan automáticamente los tres gráficos. El botón **Generar gráficos desde JSON** permite reutilizar informes anteriores.

El panel **Dashboard de ejecución** se actualiza durante la prueba con una línea de latencia por solicitud y barras de distribución de códigos HTTP. La vista previa de la distribución está disponible en [docs/dashboard-preview.svg](docs/dashboard-preview.svg).

### Escenarios de estrés

La GUI y el motor comparten un selector de escenarios. Los parámetros específicos se introducen como JSON en la sección **Tipo de prueba**:

| Orden | Escenario | Parámetros principales |
|---:|---|---|
| 1 | **Spike Test** | `peak_users` para el pico de usuarios |
| 2 | **Stress Test progresivo** | `stages`, por ejemplo `[10, 25, 50, 100]` |
| 3 | **Soak Test** | Usa `duration` y `users` como carga sostenida |
| 4 | **Escenarios mixtos** | `mix` con `url`, `method`, `weight`, `headers` y `body` |
| 5 | **Recuperación y fallos** | `failure_url` opcional; por defecto usa un fallo local controlado |
| 6 | **Prueba específica de base de datos** | `query_type` para etiquetar la consulta |
| 7 | **Payload y rate limiting** | `payload_size`, `payload_seed` y `rate_limit` |

Ejemplos de parámetros:

```json
{"peak_users": 250}
```

```json
{"stages": [10, 25, 50, 100]}
```

```json
{"mix": [
  {"url": "http://localhost:8000/api/products", "weight": 70},
  {"url": "http://localhost:8000/api/orders", "method": "POST", "weight": 30, "body": "{\"items\": []}"}
]}
```

```json
{"payload_size": 4096, "payload_seed": "sample", "rate_limit": 50}
```

Cada modo genera el mismo formato de métricas agregadas y conserva `scenario` y `phases` en el JSON para que el dashboard y los gráficos puedan comparar fases. La prueba de recuperación usa por defecto `127.0.0.1:1` para producir un fallo controlado sin enviar tráfico a terceros.

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

También se puede ejecutar un escenario desde la terminal:

```bash
loadtest https://staging.example.com/health \
  --scenario "Spike Test" \
  --scenario-params '{"peak_users":250}' \
  --users 20 --duration 60 --output reports/spike.json
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

Versión `0.4.0`, con siete escenarios de estrés, interfaz Tkinter, dashboard en vivo, exportación JSON y gráficos PNG/SVG.
