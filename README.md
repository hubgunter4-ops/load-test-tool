

## Selección de motor en GUI y TUI

La GUI Tkinter y la TUI comparten el selector **Motor de ejecución**:

- **Integrado**: ejecuta los escenarios HTTP/UDP internos.
- **Wrk**, **Wrk2**, **Pktgen**, **SlowHTTPTest** e **iPerf3**: construyen y guardan un plan reproducible con los parámetros seleccionados.

Los motores externos permanecen en modo plan por defecto; no se genera tráfico desde la GUI/TUI sin una acción explícita posterior del operador.

## Nuevos escenarios seleccionables

- **Pruebas de Conmutación por Error (Failover)**: ejecuta una fase contra el primario y conmuta a un fallback en el instante indicado por `switch_after`.
- **Estrés de Auto-escalado**: incrementa usuarios desde `min_users` hasta `max_users` en `steps` niveles y después reduce la carga hasta el mínimo.

Ejemplo de parámetros para Failover:

```json
{
  "primary_url": "http://localhost:8000/health",
  "fallback_url": "http://127.0.0.1:1/fallback",
  "switch_after": 15
}
```

Ejemplo de parámetros para Auto-escalado:

```json
{
  "min_users": 10,
  "max_users": 100,
  "steps": 4
}
```
