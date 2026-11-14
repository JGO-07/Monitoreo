# Umbrales, referencia y reglas de confianza

## Política de zonas

| Zona | Condición | Significado |
| --- | --- | --- |
| `low` (baja confianza) | `score < 0.15` | Se registra, se captura siempre y cuenta para las reglas de alerta. |
| `doubt` (duda) | `0.15 ≤ score < 0.40` | Caso dudoso; se captura 1 de cada 3. |
| `accepted` (aceptada por la política) | `score ≥ 0.40` | Supera el umbral superior. **No significa que la predicción sea correcta.** |
| estado `error` / `no_score` | inferencia fallida o sin puntuación | Estado aparte, sin zona, fuera de los denominadores. |

`score` es la probabilidad top-1 del softmax de MobileNetV3-Large (IMAGENET1K_V2). Es una puntuación del modelo, no una
probabilidad calibrada de acierto. La igualdad con el umbral inferior pertenece a duda y la igualdad con el superior, a aceptada.

## Cómo se eligieron (observaciones de referencia)

1. Se grabó `data/escenarios/referencia/` con la webcam en condiciones representativas: buena luz y teclado, rollo
   de papel y lentes de sol mostrados unos 15 s cada uno, con transiciones entre objetos (240 frames, 4 fps).
2. Se reprodujo como stream con la configuración provisional:
   corrida `run-referencia-propuesta-20261114T030644` (`config_ref mon-v1-34ccad8a`, umbrales provisionales 0.30/0.60), con 120 inferencias
   reales (muestreo 1 de cada 2) en 6 ventanas de 10 s con 20 inferencias cada una.
3. `python -m monitoreo.cli reference propose --store evidencias/almacen --run-id run-referencia-propuesta-20261114T030644`
   produjo [`evidencias/referencia/propuesta_umbrales.json`](../evidencias/referencia/propuesta_umbrales.json), que
   incluye los cuantiles, el desglose por clase, los scores por `event_id` y la evaluación de los pares candidatos. Los scores no dependen de los
   umbrales, por lo que este análisis es válido aunque la corrida usara los provisionales.

Observaciones clave de la referencia:

* Distribución: p05 = 0.059, p10 = 0.067, p25 = 0.131, p50 = 0.310, p75 = 0.464, p90 = 0.525 y media = 0.300. Estos pesos
  (entrenados con *label smoothing*) dan puntuaciones comprimidas, por lo que 0.60/0.80 dejaría casi todo en baja confianza.
* Con el objeto visible y estable, los scores están entre ~0.3 y 0.76 (`toilet tissue` 0.24–0.76, `sunglass` 0.27–0.52, `space bar`
  0.09–0.53). En las transiciones, cuando solo se ve la hoja de fondo o la mano, salen `envelope`, `notebook` o `refrigerator`
  entre 0.04 y 0.16.

| inferior | superior | % low | % doubt | % accepted | peor ventana (low_rate) |
| --- | --- | --- | --- | --- | --- |
| 0.10 | 0.40 | 18.3 | 50.0 | 31.7 | 0.30 |
| **0.15** | **0.40** | **27.5** | **40.8** | **31.7** | **0.45** |
| 0.20 | 0.40 | 35.8 | 32.5 | 31.7 | 0.65 |
| 0.20 | 0.45 | 35.8 | 38.3 | 25.8 | 0.65 |
| 0.25 | 0.45 | 44.2 | 30.0 | 25.8 | 0.75 |

**Decisión (fijada antes de ejecutar C1 y C2):**

* **Inferior = 0.15.** Separa los frames sin objeto reconocible (0.04–0.16) de los que muestran el objeto (≥ ~0.2).
* **Superior = 0.40.** Deja como «aceptadas» las vistas claras (≈ p70 de la referencia: rollo de frente y lentes de frente).
  El teclado en ángulo (~0.3–0.4) queda en duda.
* **Alerta de baja confianza ≥ 0.60 de la ventana.** La peor ventana de la referencia fue 0.45, así que el margen es de 0.15 para no
  alertar en operación normal. Es crítica si llega a ≥ 0.85.

## Referencia para el cambio sostenido

Después de fijar los umbrales, se **volvió a ejecutar** la misma secuencia de referencia con la configuración final, y
`reference build` generó [`config/referencia.json`](../config/referencia.json). Ese archivo contiene la proporción de baja confianza,
la proporción por zona, la media, los cuantiles, el histograma, la proporción por ventana y las clases más frecuentes. `build` falla si la corrida usó otra
`config_ref` u otro modelo. Su hash (`reference_ref`) queda en cada corrida (`runs.reference_ref`) y en la evidencia de cada alerta.

## Ventanas, mínimo de observaciones y reglas

* **Ventanas:** son temporales, consecutivas y de **10 s**, alineadas con el inicio de la corrida y medidas sobre el instante de recepción del frame.
  Con 4 fps y muestreo 1 de cada 2 hay ~20 inferencias por ventana.
* **Mínimo:** **10** inferencias elegibles (`status=ok` con score). Con menos, la ventana queda en estado `insufficient`:
  **no es normalidad**, no resuelve alertas activas e interrumpe la persistencia de las reglas.
* `baja-confianza-v1`: `n_low / n_eligible ≥ 0.60` en una ventana suficiente → alerta `warning` (`critical` si ≥ 0.85).
* `cambio-sostenido-v1`: `low_rate_ventana − low_rate_referencia ≥ 0.25` durante **3 ventanas suficientes
  consecutivas** → alerta `warning`. Indica un posible cambio de condiciones; la confianza no confirma drift ni pérdida de calidad.
* `evidencia-insuficiente-v1`: **2 ventanas completas consecutivas** en `insufficient` → alerta `info`.
* Mientras una alerta está activa, las ventanas que siguen en incumplimiento la actualizan y no se crean alertas repetidas.
  Una ventana `normal` la resuelve.

Para comparar corridas se mantienen el mismo modelo (`model_ref`), el mismo preprocesamiento y la misma `config_ref`. Si cualquiera de ellos
cambia, las series se separan por `config_ref` y `model_ref`.
