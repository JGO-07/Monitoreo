# Índice de evidencias · Proyecto 6

**Estudiante:** Josue Godoy Orozco (752666) · **Repositorio:** <https://github.com/JGO-07/Monitoreo> · rama `main`

## Revisiones Git

| Revisión | Qué contiene |
| --- | --- |
| `6eb658c` | Código y configuración con los umbrales finales; ejecutó la corrida de referencia final `run-referencia-20261114T031049`. |
| `3a0b5da` | **Código ejecutado en C1 y C2** (árbol limpio en `src/`, `config/` y `models/`; `runs.git_dirty = 0`). Contiene el portal, las métricas y el manifiesto de C2 con `condition.onset_s`. |
| `6d58b44` | Evidencias de C1/C2 agregadas **después** de la ejecución: store, exportaciones, recálculo y capturas del portal. No cambia el código ejecutado. |
| `84a9541` | Código que ejecutó S-01 (manifiesto con `gaps` y verificador de expectativas). |
| Commit que agrega este índice | Documentación (README, `docs/extras.md`), recálculo de métricas adicionales, demostración en vivo (F-01) y corrección de `metrics.py`: la corrida de referencia ya no toma como referencia el archivo actual, solo la registrada por la corrida. No modifica los resultados de C1/C2 (`recalculo.txt` 18/18 OK). |

Cada corrida guarda su propia revisión en `runs.git_commit`, visible en `/corridas/<run_id>` y en `run.json`.

## Ubicación de los datos

| Ruta | Contenido |
| --- | --- |
| `evidencias/almacen/monitoreo.db` | Store SQLite con las corridas evaluadas: `referencia-propuesta`, `referencia`, `c1_normal`, `c2_baja_confianza` y `s01_interrupcion` (extra). |
| `evidencias/almacen/capturas/<run_id>/` | Capturas vinculadas por `image_ref` y `image_sha256`; sin expiración. |
| `evidencias/escenarios/<escenario>/` | Exportación por corrida: `run.json`, `config.yaml`, `frames.csv`, `events.jsonl`, `window_evaluations.csv`, `resource_samples.csv`, `source_events.jsonl`, `alerts.json`, `metricas.json`, `recalculo.txt` y `verificacion.txt` (expectativas del manifiesto verificadas con `scripts/verificar_escenario.py`: C1 2/2, C2 1/1, S-01 5/5). |
| `evidencias/referencia/propuesta_umbrales.json` | Análisis de referencia usado para fijar los umbrales. |
| `evidencias/modelo/` | Manifiesto del modelo y primeras inferencias. |
| `evidencias/metricas/inventario.json` | Definiciones de todas las métricas (exportadas de `REGISTRY`). |
| `evidencias/portal/*.jpg` | Recorrido del portal (capturas de pantalla). |
| `evidencias/extras/en_vivo/` | Store de la demostración en vivo (F-01). |
| `data/escenarios/{referencia,c1_normal,c2_baja_confianza}/` | Entradas: 240 frames de webcam cada una, más `GRABACION.json`. |

## Identificadores clave

| Elemento | ID |
| --- | --- |
| Configuración (C1, C2 y referencia final) | `mon-v1-f63818a0` (umbrales 0.15/0.40) |
| Configuración provisional (solo para el análisis de umbrales) | `mon-v1-34ccad8a` (umbrales 0.30/0.60) |
| Referencia | `ref-28147cbc` = SHA-256 de [`config/referencia.json`](../config/referencia.json), construida desde `run-referencia-20261114T031049` |
| Modelo | `mobilenet_v3_large/IMAGENET1K_V2@sha256:5c1a416349c4` |
| Fuente | `webcam-escritorio-01` |
| C1 | `run-c1_normal-20261114T032144` |
| C2 | `run-c2_baja_confianza-20261114T032255`; alertas `alert-c2_baja_confianza-20261114T032255-001` (warning) y `-002` (critical) |
| S-01 (extra) | `run-s01_interrupcion-20261114T034828` (commit `84a9541`); alerta `alert-s01_interrupcion-20261114T034828-001` (info) |

## Criterios → evidencia

| Criterio | Evidencia |
| --- | --- |
| **R1.1** Stream con frecuencia y muestreo | README § Fuente visual y muestreo; `escenarios/*.yaml` (`fps: 4`); `config/monitoreo.yaml` (`sampling`); `frames.csv` (outcome por frame); G2 base = 4.0 fps medidos. |
| **R1.2** Inferencias reales atribuibles | `events.jsonl` (top-5 y `model_ref` = hash real del archivo); los scores se reproducen en otra corrida (la referencia provisional y la final dan scores idénticos; el demo en vivo y C1 dan 48/120 low); `evidencias/modelo/primera_inferencia_*.json`. |
| **R1.3** Identidad del modelo | README § Modelo; `evidencias/modelo/manifiesto_modelo.json`; `runs.model_json` en cada `run.json`. |
| **R2** Métricas G1–G7 | `docs/metricas.md` (aplicabilidad G = 7, base y definiciones); `evidencias/escenarios/*/metricas.json`; `scripts/recalcular_metricas.py`; `recalculo.txt` (7/7 base y 11/11 adicionales OK). |
| **R3.1** Umbrales justificados | `docs/umbrales.md`; `evidencias/referencia/propuesta_umbrales.json`; commit `6eb658c` anterior a C1/C2. |
| **R3.2** Tres zonas y baja confianza conservada | `src/monitoreo/policy.py`; `events.zone` y umbrales por evento; todas las `low` capturadas (C2: 77 low → 77 capturas low). |
| **R3.3** Referencia, ventanas, mínimo y regla sostenida | `config/referencia.json`; `config/monitoreo.yaml` (`windows`, `alerts.sustained_change`); `window_evaluations.csv` (estados `insufficient`/`normal`/`breach` y racha); `tests/test_rules.py`. |
| **R4.1** Eventos completos | `events.jsonl`; README § Contrato; G5 base = 1.0 en las tres corridas. |
| **R4.2** Persistencia y consulta en el portal | `evidencias/almacen/monitoreo.db`; `/eventos` y `/eventos/<id>` (`portal/04`, `05`, `06`). |
| **R4.3** Imágenes vinculadas sin expiración | README § Captura; `evidencias/almacen/capturas/`; M-08 = 1.0 (SHA-256 verificado). |
| **R5.1** Reglas explícitas | `config/monitoreo.yaml` (`alerts`, `windows`); `docs/umbrales.md`; `src/monitoreo/rules.py`. |
| **R5.2** Alertas desde observaciones reales | `evidencias/escenarios/c2_baja_confianza/alerts.json` (eventos, ventanas, `query`, `config_ref`, `reference_ref`). |
| **R5.3** Alertas en el portal | `/alertas` y `/alertas/<id>` (`portal/01`, `02`, `03`): momento, motivo, severidad y acción sugerida. |
| **R6.1** C1 | Sección C1 abajo; `evidencias/escenarios/c1_normal/`; `portal/05`, `07`. |
| **R6.2** C2 | Sección C2 abajo; `evidencias/escenarios/c2_baja_confianza/`; `portal/01`–`04`, `06`, `08`. |
| **R7.1** Instalación y comandos | README § Instalación y § Uso. |
| **R7.2** Evidencias verificables | Este índice; las exportaciones y `recalculo.txt`; los pasos de recálculo están abajo; [`reproduccion/c2_clon_limpio.log`](reproduccion/c2_clon_limpio.log): C2 reproducido desde un clon limpio del commit `c516219` con venv nuevo, con resultados idénticos (77/40/3, mismas ventanas y alertas, 18/18 métricas OK). |
| **R7.3** Autoría y procedencia | README § Autoría, aportación propia y componentes reutilizados. |
| **Extras** | `docs/extras.md` (F-01..F-06, M-01..M-11, S-01). |

## C1 · Operación normal

* **Entrada:** `data/escenarios/c1_normal/` (240 frames de webcam con buena luz; teclado, rollo de papel y lentes).
* **Comando:** `python -m monitoreo.cli run --scenario-file escenarios/c1_normal.yaml --store evidencias/almacen`
* **Configuración:** `mon-v1-f63818a0`, umbrales 0.15/0.40, ventana de 10 s, mínimo de 10 observaciones, alerta ≥ 0.60.
* **Esperado:** inferencias reales, zonas mezcladas como en la referencia y ninguna alerta de baja confianza ni de cambio sostenido.
* **Observado:** 240 frames (120 inferidos y 120 descartados por muestreo, 0 errores); 48 low, 31 doubt y 41 accepted.
  low_rate por ventana: 0.25, 0.30, 0.30, 0.50, 0.50 y 0.55 (todas < 0.60), **sin alertas**. La regla sostenida tuvo una ventana
  en incumplimiento en 50–60 s (0.55 ≥ 0.525), con racha 1 de 3, así que no alertó. La última ventana (60 s, parcial y vacía) quedó `insufficient`.
  Métricas: p95 de inferencia 32.9 ms, 4.0 fps de captura, CPU 53.7 % de un núcleo, trazabilidad 1.0 y versión 1.0.
* **Nivel:** observado y reproducido (ejecutado por el autor con código, configuración y modelo identificados; métricas recalculadas).

## C2 · Baja confianza

* **Entrada:** `data/escenarios/c2_baja_confianza/` (misma escena con oclusión, desenfoque o movimiento y menos luz). Los scores
  vienen de la inferencia real y no se modificaron.
* **Comando:** `python -m monitoreo.cli run --scenario-file escenarios/c2_baja_confianza.yaml --store evidencias/almacen`
* **Configuración:** la misma que C1 (`mon-v1-f63818a0`), fijada antes de la demostración.
* **Esperado:** predomina la zona low, se conservan capturas y eventos, y se dispara `baja-confianza-v1` cuando `low_rate ≥ 0.60` en una ventana suficiente.
* **Observado:** 77 low, 40 doubt y 3 accepted. low_rate por ventana: 0.70, 0.65, 0.35, 0.85, 0.85 y 0.45.
  * `alert-…-001` (**warning**) se disparó a los 11.6 s por la ventana 0–10 s (14/20 = 0.70), se extendió con la de 10–20 s (0.65) y
    se resolvió con la ventana normal de 20–30 s (0.35).
  * `alert-…-002` (**critical**, 17/20 = 0.85 ≥ 0.85) se disparó a los 41.6 s por la ventana 30–40 s, se extendió con la de 40–50 s y
    se resolvió con la de 50–60 s (0.45). Tiene 40 eventos vinculados y 36 capturas.
  * La regla sostenida llegó dos veces a 2 ventanas consecutivas, sin las 3 requeridas, y no alertó.
  * El tiempo de detección (M-11) fue de 11.56 s.
* **Nivel:** observado y reproducido.

## Muestra trazable (de la imagen a la alerta)

1. Frame `data/escenarios/c2_baja_confianza/frame_00124.jpg`, recibido como `run-c2_baja_confianza-20261114T032255-f000124`
   (`frames.csv`, outcome `inferred`).
2. Evento `evt-c2_baja_confianza-20261114T032255-000124` (`events.jsonl`): Windsor tie con score 0.0609 < 0.15, zona `low`, inferencia de 27.8 ms,
   `config_ref mon-v1-f63818a0`.
3. Captura `evidencias/almacen/capturas/run-c2_baja_confianza-20261114T032255/run-c2_baja_confianza-20261114T032255-f000124.jpg`
   (SHA-256 `cf104884019a…`, verificado por G5).
4. Ventana 30–40 s de `baja-confianza-v1` (`window_evaluations.csv`): 17/20 = 0.85, `breach`.
5. Alerta `alert-c2_baja_confianza-20261114T032255-002` (critical): su `event_ids` incluye el evento y su `query` lo devuelve.
6. Portal: `/eventos/evt-c2_baja_confianza-20261114T032255-000124` (`portal/06`) → enlace a la alerta.

## Cómo recalcular una muestra

```bash
python -I scripts/recalcular_metricas.py evidencias/escenarios/c2_baja_confianza --store evidencias/almacen
```

Para comprobar a mano la proporción de baja confianza de C2:

```bash
python -c "import json; e=[json.loads(l) for l in open('evidencias/escenarios/c2_baja_confianza/events.jsonl')]; s=[x['score'] for x in e if x['status']=='ok']; print(sum(v<0.15 for v in s), len(s))"
```

El resultado esperado es `77 120`, es decir, 0.6417.
