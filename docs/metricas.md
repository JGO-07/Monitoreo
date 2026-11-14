# Inventario de métricas G1–G7

Los siete grupos **aplican** (G = 7). No se excluye ninguno. Cada grupo tiene **una métrica base**; las demás se
registran como **adicionales** y se reclaman por separado en [`extras.md`](extras.md).

Las definiciones ejecutables (texto, fórmula, población, ventana, exclusiones, tratamiento de cero observaciones e
instrumentación) están en [`src/monitoreo/metrics.py`](../src/monitoreo/metrics.py) (`REGISTRY`). Se exportan a
[`evidencias/metricas/inventario.json`](../evidencias/metricas/inventario.json) y el portal las muestra en `/metricas`.

## Aplicabilidad y métrica base

| Grupo | Aplica | Justificación | Métrica base | Unidad |
| --- | --- | --- | --- | --- |
| G1 Salud y rendimiento | Sí | Hay procesamiento de inferencias (sin endpoint HTTP de inferencia: el pipeline invoca el modelo directamente). | `G1-tiempo-inferencia-p95` | ms |
| G2 Captura del stream | Sí | La secuencia grabada con la webcam o la propia webcam se reproduce como stream a un FPS declarado. | `G2-fps-captura` | frames/s |
| G3 Recursos | Sí | Se mide la CPU del proceso con psutil. No hay GPU, pero eso no excluye el grupo. | `G3-cpu-proceso` | % de un núcleo |
| G4 Predicciones | Sí | Hay scores top-1 por frame; no requiere etiquetas. | `G4-proporcion-baja-confianza` | proporción |
| G5 Datos y trazabilidad | Sí | Hay eventos e imágenes vinculadas. | `G5-cobertura-trazabilidad` | proporción |
| G6 Fuentes y versiones | Sí | Hay una fuente (`webcam-escritorio-01`) y una versión de modelo, ambas en cada evento. | `G6-version-esperada-vs-reportada` | proporción |
| G7 Alertas | Sí | Hay reglas y alertas persistidas. | `G7-alertas-activas` | alertas |

## Definición de las métricas base (M1)

| ID | Qué mide | Fórmula | Población | Ventana | Exclusiones | Cero observaciones |
| --- | --- | --- | --- | --- | --- | --- |
| `G1-tiempo-inferencia-p95` | Duración del *forward* del modelo, separada de captura, cola, preprocesamiento y persistencia | p95 con interpolación lineal (`h=(n−1)·0.95`) de `events.inference_ms` | eventos `status=ok` | corrida completa | eventos `error`/`no_score` | `null`, `status=sin_observaciones` |
| `G2-fps-captura` | Frames entregados por la fuente por segundo, antes del muestreo | `(n−1) / (received_at_último − received_at_primero)` | frames con `outcome ≠ read_error` (incluye los descartados por muestreo) | corrida completa | errores de lectura | `n<2 → null` |
| `G3-cpu-proceso` | CPU del proceso de monitoreo (% de un núcleo; puede superar 100 %) | media de `resource_samples.process_cpu_pct` | muestras de 1 s durante la corrida | corrida completa | primera lectura de psutil (cebado) | `null` |
| `G4-proporcion-baja-confianza` | Proporción de frames con score < umbral inferior (describe salidas, no aciertos) | `n(score < lower) / n(status=ok con score)` | eventos `ok` con score; unidad = frame inferido | corrida completa; también por ventana de 10 s en `window_evaluations` | `error`/`no_score` | `null` = evidencia insuficiente, nunca 0 |
| `G5-cobertura-trazabilidad` | Eventos con campos requeridos y referencias resolubles | `n(completos)/n(eventos)`, donde completo = campos obligatorios + frame↔evento + config existente + captura existente con SHA-256 correcto (si se reclamó) | todos los eventos | corrida completa | ninguna | `null` |
| `G6-version-esperada-vs-reportada` | Concordancia entre la versión configurada y la reportada | `n(model_ref == model.expected_model_ref) / n(eventos)` | todos los eventos | corrida completa | ninguna | `null` |
| `G7-alertas-activas` | Alertas abiertas al cierre de la corrida (además, máximo simultáneo y alertas disparadas) | `n(alertas sin resolved_at o resueltas después de runs.ended_at)` | alertas de la corrida | instante de cierre | ninguna | 0 es un valor válido; las ventanas evaluadas prueban que las reglas corrieron |

## Adquisición e instrumentación (M2)

| Fuente primaria | Instrumentación |
| --- | --- |
| `frames` | `Pipeline._produce`: una fila por frame entregado por la fuente (`received_at = time.time()`), con `outcome` ∈ {inferred, dropped_sampling, dropped_saturation, read_error}. |
| `events` | `Pipeline._process`: `time.perf_counter()` alrededor de la transformación (`preprocess_ms`) y de `net(x)` (`inference_ms`); `queue_ms`, `e2e_ms`, `model_ref` (hash real del archivo cargado), `config_ref`, umbrales usados, zona, top-5, captura con SHA-256. |
| `resource_samples` | `Pipeline._sample_resources`: psutil cada 1 s (CPU y RSS del proceso, CPU y RAM del sistema, cola, disco libre). |
| `window_evaluations`, `alerts` | `RuleEngine`: una fila por ventana y regla, más las alertas con su evidencia. |
| Prometheus `/metrics` | `prometheus_client` en vivo (contadores, histogramas y gauges equivalentes); es complementario. |

## Cálculo, trazabilidad y verificación (M3–M5)

* **Cálculo:** `python -m monitoreo.cli metrics --store evidencias/almacen --run-id <run_id>` (también `/corridas/<run_id>` y
  `/api/runs/<run_id>/metrics`). Cada resultado incluye `n`, `status` (por ejemplo `sin_observaciones`), la consulta SQL usada y
  `evidence_ids` con las observaciones primarias que lo respaldan (por ejemplo, los eventos con mayor `inference_ms` en el p95).
* **Exportación:** `python -m monitoreo.cli export ... --out evidencias/escenarios/<escenario>` genera `frames.csv`, `events.jsonl`,
  `resource_samples.csv`, `window_evaluations.csv`, `alerts.json`, `run.json`, `config.yaml` y `metricas.json`.
* **Recálculo independiente:** `python scripts/recalcular_metricas.py evidencias/escenarios/<escenario> --store evidencias/almacen`
  recalcula las 7 métricas base solo con la biblioteca estándar, a partir de los archivos exportados. G4 se recalcula desde el score y el
  umbral, no desde la zona guardada. G5 vuelve a leer cada captura y verifica su SHA-256. Sale con código ≠ 0 si alguna difiere.
  Los resultados del recálculo están en `evidencias/escenarios/<escenario>/recalculo.txt`.

## Métricas adicionales (reclamadas en `extras.md`)

| ID | Grupo | Descripción breve |
| --- | --- | --- |
| `G1-throughput` | G1 | Inferencias terminadas por segundo. |
| `G1-tasa-errores` | G1 | `n(error)/n(eventos)`. |
| `G2-frames-descartados` | G2 | Proporción de frames no inferidos, separada por motivo (muestreo o saturación). |
| `G2-latencia-e2e-p95` | G2 | Latencia de extremo a extremo, desde la recepción hasta el evento terminado (mismo reloj). |
| `G3-ram-proceso` | G3 | RSS máximo del proceso. |
| `G4-predicciones-por-clase` | G4 | Distribución de la clase top-1. |
| `G4-psi-confianza-vs-referencia` | G4 | PSI de la distribución de scores frente a la referencia (10 bins). |
| `G5-imagenes-vinculadas` | G5 | Capturas existentes con SHA-256 correcto sobre las capturas reclamadas. |
| `G5-entradas-invalidas` | G5 | `n(read_error)/n(frames)`. |
| `G6-tiempo-ultimo-reporte` | G6 | Frescura de la fuente al cierre. |
| `G7-tiempo-deteccion` | G7 | Desde el inicio declarado de la condición hasta la primera alerta. |
