

## Ejecución en bucle

La GUI y la TUI incluyen la casilla **✅ Ejecutar acciones en bucle**. Al activarla se pueden indicar:

- **Iteraciones**: entre 1 y 100 ciclos.
- **Pausa entre ciclos**: entre 0 y 3.600 segundos.

El bucle repite la acción completa seleccionada, conserva las fases de cada ciclo en el informe y permite detenerse mediante el control **Detener**. La casilla está desactivada por defecto y no admite bucles infinitos.

La CLI equivalente es:

```bash
loadtest-cli https://staging.example.com/health \
  --loop --loop-count 3 --loop-delay 5
```

Para planes de motores externos, la selección de bucle queda registrada en el JSON del plan; la ejecución externa continúa requiriendo una acción explícita y autorización del operador.
