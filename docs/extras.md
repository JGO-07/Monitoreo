# Inventario de extras reclamados

Cada adicional tiene un ID único, su tipo (F = funcionalidad, M = métrica, S = escenario), el nivel que se solicita, la
complejidad que acredita y su evidencia. Lo que ya forma parte de la base **no** se reclama: las vistas mínimas de eventos y alertas, los
vínculos evento→captura→configuración, los dos umbrales, las capturas obligatorias, C1/C2, la regla sostenida configurada y
las siete métricas base.

## F · Funcionalidades

| ID | Descripción | Nivel solicitado | Complejidad acreditada | Evidencia | Doble conteo |
| --- | --- | --- | --- | --- | --- |
| F-01 | **Visualización en vivo del stream con resultados**: `serve --live` ejecuta el pipeline en un hilo; `/en-vivo` muestra un MJPEG de los frames inferidos y el último evento (clase, score, zona, tiempo y enlace al evento). | 5 | Flujo adicional completo con datos reales: corrida `run-en_vivo_demo-20261114T032930` (120 eventos persistidos en su propio store). | [`evidencias/portal/09_en_vivo_t1.jpg`](../evidencias/portal/09_en_vivo_t1.jpg), [`10_en_vivo_t2.jpg`](../evidencias/portal/10_en_vivo_t2.jpg) (seq 32 → 56), store [`evidencias/extras/en_vivo/`](../evidencias/extras/en_vivo/), `src/monitoreo/portal.py` (`/en-vivo`, `/en-vivo/stream.mjpg`), `templates/live.html`. Se ejecutó con el commit `6d58b44` y `git_dirty=1`: el único cambio pendiente estaba en `metrics.py` (cálculo de PSI), no en el pipeline, y se incluyó en el commit siguiente. | Es distinta de la consulta de eventos (base). |
| F-02 | **Filtros avanzados de eventos**: corrida, zona, estado, clase (subcadena), fuente, con o sin captura, rango de fechas y paginación, con los mismos filtros en `/api/events`. | 10 | Consulta parametrizada sobre la persistencia SQLite, verificación de resultados, estado vacío ("Ningún evento cumple los filtros") y estado erróneo (fecha inválida → 400; evento inexistente → 404). | `tests/test_portal.py::test_filtros_y_estados_vacios`; [`evidencias/portal/04_eventos_c2_low_con_captura.jpg`](../evidencias/portal/04_eventos_c2_low_con_captura.jpg) (zona=low, con captura, corrida C2: 77 eventos). | La base solo exige consultar eventos; aquí se reclama la parametrización avanzada. |
| F-03 | **Exportación de eventos a CSV** que respeta los filtros activos (`/api/events.csv`, enlace "exportar CSV"). | 5 | Flujo sencillo y completo con datos reales. | `tests/test_portal.py` (encabezado CSV); enlace en `/eventos` y en `/corridas/<run_id>`. | Es distinta de F-02 (consulta) y de la exportación por CLI usada como evidencia. |
| F-04 | **Gráfica histórica por ventana**: SVG con la proporción de baja confianza por ventana de 10 s, color por estado (normal, incumplimiento, insuficiente), línea del umbral de alerta y línea de la referencia. | 5 | Flujo sencillo y completo con datos reales (`window_evaluations`). | [`evidencias/portal/07_corrida_c1_ventanas.jpg`](../evidencias/portal/07_corrida_c1_ventanas.jpg), [`08_corrida_c2_ventanas.jpg`](../evidencias/portal/08_corrida_c2_ventanas.jpg). | La métrica G4 base se reclama aparte como métrica, sin doble conteo; aquí se reclama la visualización. |
| F-05 | **Revisión de capturas por alerta**: galería con todas las capturas de los eventos que originaron la alerta, con score, zona y clase, y enlace a cada evento. | 5 | Flujo sencillo y completo con datos reales (alerta `…-002`: 40 eventos, 36 capturas). | [`evidencias/portal/03_alerta_c2_002_capturas.jpg`](../evidencias/portal/03_alerta_c2_002_capturas.jpg). | La base exige el vínculo alerta→eventos; la galería de revisión es adicional. |
| F-06 | **Dashboard de resumen**: alertas activas, conteo por zona y errores por corrida, últimas alertas. | 5 | Flujo sencillo y completo con datos reales. | Página `/` del portal (`templates/index.html`). | No repite F-04 (gráfica) ni F-02 (filtros). |

## M · Métricas adicionales

Todas se recalculan de forma independiente con `scripts/recalcular_metricas.py`: 18 de 18 coinciden en las tres corridas
(`evidencias/escenarios/*/recalculo.txt`). Las definiciones completas están en `src/monitoreo/metrics.py` y en `/metricas`.

| ID | Grupo | Nivel solicitado | Complejidad acreditada | Valores (ref · C1 · C2) |
| --- | --- | --- | --- | --- |
| M-01 `G1-throughput` | G1 | 5 | Tasa derivada (inferencias/s) sobre la población `ok`; n < 2 → null. | 2.001 · 2.001 · 2.001 |
| M-02 `G1-tasa-errores` | G1 | 5 | Proporción con población (todos los intentos) y exclusión (frames no inferidos); n = 0 → null. | 0 · 0 · 0 |
| M-03 `G2-frames-descartados` | G2 | 5 | Proporción separada por motivo (muestreo o saturación) en `by_reason`. | 0.5 · 0.5 · 0.5 (todo por muestreo) |
| M-04 `G2-latencia-e2e-p95` | G2 | 5 | Percentil de una duración derivada (recepción → evento terminado, mismo reloj). | 40.9 · 41.5 · 46.7 ms |
| M-05 `G3-ram-proceso` | G3 | 3 | Agregado sencillo (máximo de RSS). | 360.4 · 359.2 · 360.0 MB |
| M-06 `G4-predicciones-por-clase` | G4 | 5 | Distribución por clase (unidad: frame inferido). | 25 · 24 · 49 clases distintas |
| M-07 `G4-psi-confianza-vs-referencia` | G4 | 8 | Compara la distribución de scores con la referencia (10 bins, piso 1e-4). Caso controlado de cambio: C2 (condiciones degradadas) da 1.89 frente a 0.38 de C1, y la referencia sin referencia previa da null. Límite documentado: con n = 120 el PSI es ruidoso (C1 > 0.25 sin alerta). | null · 0.380 · 1.891 |
| M-08 `G5-imagenes-vinculadas` | G5 | 5 | Proporción de capturas reclamadas que existen y tienen el SHA-256 correcto. | 1 · 1 · 1 (53 · 62 · 92 capturas) |
| M-09 `G5-entradas-invalidas` | G5 | 5 | Proporción de `read_error` entre los frames recibidos; el caso no nulo se verifica en `tests/test_pipeline.py` con un archivo corrupto. | 0 · 0 · 0 |
| M-10 `G6-tiempo-ultimo-reporte` | G6 | 3 | Indicador sencillo por fuente al cierre. | 0.66 · 0.68 · 0.69 s |
| M-11 `G7-tiempo-deteccion` | G7 | 5 | Duración derivada entre el inicio declarado de la condición (manifiesto de C2, `onset_s: 0`) y la primera alerta; sin condición → null. | — · — · 11.56 s |

Doble conteo: ninguna es la métrica base de su grupo, y ninguna es una variación de umbral, ventana o clase de otra. El p50 que
acompaña a los p95 es de la misma familia y no se reclama.

## S · Escenarios adicionales

Por ahora no se presentan escenarios adicionales. Los candidatos son la interrupción del stream con recuperación (regla
`evidencia-insuficiente-v1`) y el cambio sostenido frente a la referencia (`cambio-sostenido-v1`). Si se agregan,
se registrarán aquí con su evidencia.
