

## Motores externos opcionales

La CLI incluye adaptadores para herramientas especializadas. Se detectan sin instalarlas y generan un plan reproducible. **Por defecto no se ejecuta ningún tráfico**.

```bash
# Ver disponibilidad local
loadtest tools

# Revisar el comando sin ejecutarlo
loadtest external --tool wrk --target https://staging.example.com/health \
  --connections 40 --threads 4 --duration 30

# Wrk2: tasa constante de solicitudes por segundo
loadtest external --tool wrk2 --target https://staging.example.com/health \
  --connections 40 --threads 4 --duration 30 --rate 250

# SlowHTTPTest: estrés de conexión lenta, limitado y solo con autorización
loadtest external --tool slowhttptest --target https://staging.example.com/ \
  --connections 50 --duration 30 --rate 10

# iPerf3: streams paralelos TCP o UDP hacia un servidor iPerf3 autorizado
loadtest external --tool iperf3 --target 127.0.0.1 \
  --connections 4 --duration 30 --protocol tcp

# Pktgen: generar un script de kernel pktgen, sin ejecutarlo
loadtest external --tool pktgen --target 198.18.0.2 --interface eth0 \
  --destination 198.18.0.2 --port 9000 --packet-size 512 --rate 100 --count 1000 \
  --output reports/pktgen-plan.json
```

Motores incluidos:

| Motor | Uso integrado | Requisito local |
|---|---|---|
| **Wrk** | Conexiones HTTP concurrentes, hilos y latencia | binario `wrk` |
| **Wrk2** | HTTP con tasa constante y latencias precisas | binario `wrk2` |
| **Pktgen** | Plan de generación de paquetes desde el procfs del kernel | `/proc/net/pktgen` y privilegios de red |
| **SlowHTTPTest** | Estrés de conexión lenta/Slowloris | binario `slowhttptest` |
| **iPerf3** | Throughput TCP/UDP con streams paralelos | binario `iperf3` y servidor remoto |

La ejecución real requiere dos indicadores explícitos:

```bash
loadtest external --tool wrk --target https://staging.example.com/ \
  --execute --authorized
```

Los límites integrados son conservadores: 1.000 conexiones, 300 segundos, 32 hilos, 1.000 RPS/PPS o conexiones por segundo, hasta 1.000.000 Kbit/s para iPerf3 y 1.200 bytes por paquete de Pktgen. **SlowHTTPTest y Pktgen deben usarse exclusivamente en sistemas propios o con autorización expresa**. Para Pktgen, el modo `--execute` escribe el script configurado; la activación del tráfico sigue siendo una acción manual del operador.
