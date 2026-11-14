# Proyecto 6 · Centro de monitoreo de computer vision

Portal individual que convierte un stream visual en **inferencias reales de clasificación**, **eventos persistidos con
capturas vinculadas**, **métricas por grupo (G1–G7)** y **alertas investigables**, con la evidencia necesaria para
recalcular cada resultado.

| | |
| --- | --- |
| **Autor** | Josue Godoy Orozco · matrícula 752666 · entrega individual (ITESO, Integración de Servicios de Aprendizaje Automático) |
| **Tarea** | Clasificación de imágenes, multiclase, una etiqueta por imagen |
| **Modelo** | MobileNetV3-Large preentrenado (ImageNet-1K, pesos `torchvision IMAGENET1K_V2`), sin entrenamiento propio |
| **Fuente** | Webcam de la computadora grabada como secuencia de imágenes y reproducida como stream a 4 fps; la webcam en vivo es opcional |
| **Ejecución** | Local, CPU (Windows 11, Python 3.13, torch 2.15.0+cpu) |
| **Índice de evidencias** | [`evidencias/indice.md`](evidencias/indice.md), que relaciona cada criterio con archivos, IDs y revisión Git |
| **Extras reclamados** | [`docs/extras.md`](docs/extras.md) |

## Arquitectura

```
fuente (secuencia/video/webcam, fps declarado)
   │  RawFrame(seq, received_at)                      tabla frames: un registro por frame recibido
   ▼
productor ── muestreo 1 de cada n ── cola acotada ── descarte por saturación
   ▼
consumidor: MobileNetV3 (preprocesamiento + forward medidos por separado)
   │  zona por umbrales · captura según política · evento        tablas events + capturas/
   ▼
motor de reglas: ventanas de 10 s, mínimo de observaciones,      tablas window_evaluations + alerts
   reglas baja-confianza / cambio-sostenido / evidencia-insuficiente
   ▼
portal FastAPI: eventos, alertas, corridas, configuración, métricas, en vivo, /metrics
```

| Módulo | Responsabilidad |
| --- | --- |
| [`model.py`](src/monitoreo/model.py) | Carga los pesos de `models/`, verifica su SHA-256, define el preprocesamiento, ejecuta la inferencia y expone la identidad y el entorno. |
| [`sources.py`](src/monitoreo/sources.py) | Fuentes `sequence`, `video` y `webcam` a un ritmo fijo (reloj monótono), con reconexión e interrupciones simulables. |
| [`pipeline.py`](src/monitoreo/pipeline.py) | Hilos productor y consumidor, muestreo, cola, eventos, capturas, recursos (psutil) y registro de la corrida (commit Git, config, referencia). |
| [`policy.py`](src/monitoreo/policy.py) | Dos umbrales y tres zonas; los estados `error` y `no_score` van aparte. |
| [`rules.py`](src/monitoreo/rules.py) | Ventanas temporales, estados `insufficient`/`normal`/`breach` y alertas sin duplicados. |
| [`reference.py`](src/monitoreo/reference.py) | Análisis de la referencia y construcción de `config/referencia.json`. |
| [`metrics.py`](src/monitoreo/metrics.py) | Registro de métricas G1–G7: definición y cálculo con consulta e IDs de evidencia. |
| [`export.py`](src/monitoreo/export.py), [`scripts/recalcular_metricas.py`](scripts/recalcular_metricas.py) | Exportación plana y recálculo independiente (solo biblioteca estándar). |
| [`portal.py`](src/monitoreo/portal.py) + [`templates/`](src/monitoreo/templates/) | Portal y API JSON. |
| [`store.py`](src/monitoreo/store.py) | Esquema SQLite. Un *store* es una carpeta con `monitoreo.db` y `capturas/`. |

## Autoría, aportación propia y componentes reutilizados

* **Aportación propia:** todo el código de `src/monitoreo/`, `scripts/` y `tests/`, la configuración (`config/`),
  los manifiestos de escenarios (`escenarios/`), las grabaciones de webcam (`data/escenarios/`), las corridas y los
  documentos de `docs/` y `evidencias/`. El desarrollo se hizo con asistencia de un asistente de IA (Claude Code); los
  commits lo indican con `Co-Authored-By`.
* **Reutilizado:** los pesos preentrenados MobileNetV3-Large de torchvision (BSD-3-Clause), descargados de la URL oficial
  y verificados por hash. También las bibliotecas open source de `requirements.txt` y las imágenes de muestra
  `data/muestras/{chelsea,coffee}.jpg` de `skimage.data` (BSD-3), que solo se usan para la primera inferencia y las pruebas.
* **Proyecto 5:** no se reutiliza código ni el carrito. La cámara del carrito era la fuente preferente, pero no es
  obligatoria; se eligió la webcam porque se puede grabar y versionar, y así el evaluador reproduce C1/C2 sin hardware.

## Modelo

| Campo | Valor |
| --- | --- |
| Artefacto | [`models/mobilenet_v3_large-5c1a4163.pth`](models/) (22 132 113 bytes, versionado en el repo) |
| SHA-256 | `5c1a416349c4cf298f2a6a5e2600ed0ee55e604713578f5e74e6bc8bcaef7997`; se verifica al cargar y no arranca si difiere |
| Origen | <https://download.pytorch.org/models/mobilenet_v3_large-5c1a4163.pth> (`python -m monitoreo.cli fetch-model`) |
| `model_ref` en eventos | `mobilenet_v3_large/IMAGENET1K_V2@sha256:5c1a416349c4` |
| Clases | 1000 categorías ImageNet-1K (`MobileNet_V3_Large_Weights.IMAGENET1K_V2.meta["categories"]`) |
| Preprocesamiento | BGR→RGB, resize del lado corto a 232 (bilinear), center crop 224, normalización mean (0.485, 0.456, 0.406) / std (0.229, 0.224, 0.225) |
| Salida | Softmax de 1000 clases; `score` = probabilidad top-1. **Es una puntuación del modelo, no una probabilidad calibrada de acierto.** |
| Entorno | CPU, 2 hilos de torch; el manifiesto con SO, CPU y versiones está en [`evidencias/modelo/manifiesto_modelo.json`](evidencias/modelo/manifiesto_modelo.json) |

## Fuente visual y muestreo

* **Grabación:** `python -m monitoreo.cli record --out data/escenarios/<nombre> --seconds 60 --fps 4` capturó la webcam
  (640×480) en tres sesiones: `referencia`, `c1_normal` y `c2_baja_confianza`, con 240 frames cada una. La escena
  muestra un teclado, un rollo de papel y unos lentes de sol sobre un fondo de hoja blanca.
  C2 se grabó con la misma escena, pero con oclusión con la mano, desenfoque y movimiento, y menos luz.
* **Reproducción como stream:** la fuente `sequence` entrega una imagen cada 1/4 s con un reloj monótono, según el
  manifiesto de `escenarios/*.yaml`. Cada frame recibe un `seq` y un `received_at`.
* **Política de muestreo** ([`config/monitoreo.yaml`](config/monitoreo.yaml)): se infiere **1 de cada 2** frames, así que hay
  ~2 inferencias/s y los demás quedan como `dropped_sampling`. La cola de inferencia admite 8 elementos; si está llena, el frame se
  descarta como `dropped_saturation`. Un fallo de lectura queda como `read_error`. Los frames consecutivos son parecidos,
  por lo que no se presentan como observaciones independientes; las tasas se interpretan por ventana.
* **Webcam en vivo** (opcional): `python -m monitoreo.cli serve --live --kind webcam --device 0`.

## Contrato de evento y alerta

| Campo de la guía | Campo implementado (`events`) |
| --- | --- |
| `event_id`, `run_id`, `frame_id`, `timestamp`, `source_id`, `task`, `model_ref`, `status`, `config_ref`, `image_ref` | Mismos nombres (`timestamp` es el instante de recepción del frame, en epoch s) |
| `prediction.class_id`, `prediction.score` | `class_id`, `class_name`, `score`, más `top5_json` |
| `confidence_zone` | `zone` ∈ {`low`, `doubt`, `accepted`}; `NULL` si `status` ≠ `ok` |
| (configuración reconstruible) | `lower_threshold`, `upper_threshold` en cada evento; `config_ref` → tabla `configs` (YAML y JSON canónico) |
| (tiempos) | `queue_ms`, `preprocess_ms`, `inference_ms`, `e2e_ms` |
| (integridad de la captura) | `image_sha256` |

Las alertas (`alerts`) incluyen `alert_id`, `rule_id`, `timestamp`, `severity`, `status`, `reason`, `suggested_action`,
`config_ref`, `window_start`/`window_end`, `event_ids_json`, `evidence_json` (parámetros de la regla, umbrales,
`reference_ref` y valores por ventana) y `query`, que es una consulta SQL reproducible de los eventos.

## Política de confianza, referencia y reglas

Los umbrales son **inferior 0.15 y superior 0.40**. Se fijaron con una corrida de referencia real **antes** de ejecutar
C1/C2 y su justificación está en [`docs/umbrales.md`](docs/umbrales.md). Hay ventanas de 10 s con un mínimo de 10
inferencias elegibles; si no se alcanza, la ventana queda como `insufficient`, que no equivale a normalidad.
Reglas:

| Regla | Condición | Severidad |
| --- | --- | --- |
| `baja-confianza-v1` | `low_rate ≥ 0.60` en una ventana suficiente | warning, o critical si ≥ 0.85 |
| `cambio-sostenido-v1` | `low_rate − referencia (0.275) ≥ 0.25` durante 3 ventanas suficientes consecutivas | warning |
| `evidencia-insuficiente-v1` | 2 ventanas completas consecutivas `insufficient` | info |

## Captura y conservación de imágenes

Se guardan **todas** las inferencias `low`, **1 de cada 3** `doubt` y **1 de cada 15** `accepted` como muestra normal, en
`<store>/capturas/<run_id>/<frame_id>.jpg`. Cada captura queda vinculada a su evento por `image_ref` (ruta relativa al
store) y `image_sha256`. **No existe ninguna rutina de expiración ni borrado:** las capturas de las corridas evaluadas
están versionadas en Git en `evidencias/almacen/capturas/`.

## Métricas

Los siete grupos aplican (G = 7) y cada uno tiene una métrica base: tiempo de inferencia p95 · FPS de captura · CPU del proceso ·
proporción de baja confianza · cobertura de trazabilidad · versión esperada vs reportada · alertas activas.
La definición, la adquisición, el cálculo, la trazabilidad y la verificación están en [`docs/metricas.md`](docs/metricas.md).

## Instalación

Requisitos: Python 3.11 o superior y unos 1.5 GB libres para torch CPU. Los comandos son para Windows; en Linux/macOS usa `.venv/bin/`.

```bash
git clone https://github.com/JGO-07/Monitoreo.git
cd Monitoreo
python -m venv .venv
.venv/Scripts/pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/pip install -r requirements.lock.txt
.venv/Scripts/pip install -e .
.venv/Scripts/python -m pytest -q
```

## Uso

**Ver las evidencias en el portal** (las corridas evaluadas, sin volver a ejecutar nada):

```bash
.venv/Scripts/python -m monitoreo.cli serve --store evidencias/almacen --port 8000
```

Abre <http://127.0.0.1:8000>. Las páginas son `/eventos`, `/alertas`, `/corridas`, `/metricas`, `/api/docs` y `/metrics`.

**Reproducir los escenarios** en un store nuevo, sin tocar las evidencias versionadas:

```bash
.venv/Scripts/python -m monitoreo.cli run --scenario-file escenarios/c1_normal.yaml --store var/repro
.venv/Scripts/python -m monitoreo.cli run --scenario-file escenarios/c2_baja_confianza.yaml --store var/repro
.venv/Scripts/python -m monitoreo.cli serve --store var/repro --port 8001
```

Cada corrida dura unos 60 s, que es el tiempo real de la secuencia, e imprime su `run_id` y un resumen de zonas y alertas.

**Métricas, exportación y recálculo de una corrida:**

```bash
.venv/Scripts/python -m monitoreo.cli metrics --store var/repro --run-id <run_id> --scenario-file escenarios/c2_baja_confianza.yaml
.venv/Scripts/python -m monitoreo.cli export --store var/repro --run-id <run_id> --scenario-file escenarios/c2_baja_confianza.yaml --out var/repro/export_c2
.venv/Scripts/python -I scripts/recalcular_metricas.py var/repro/export_c2 --store var/repro
```

**Referencia y umbrales** (procedimiento usado; ver `docs/umbrales.md`):

```bash
.venv/Scripts/python -m monitoreo.cli reference propose --store evidencias/almacen --run-id run-referencia-propuesta-20261114T030644
.venv/Scripts/python -m monitoreo.cli run --scenario-file escenarios/referencia.yaml --store <store>
.venv/Scripts/python -m monitoreo.cli reference build --store <store>
```

`reference build` sobrescribe `config/referencia.json`; úsalo solo si quieres regenerar la referencia.

**Otras herramientas:** `model-info`, `infer --image <ruta>`, `probe` (scores en vivo de la webcam, sin guardar), `record` y
`serve --live --scenario-file escenarios/c1_normal.yaml --store var/live` (stream en vivo en `/en-vivo`).

## Resultados de los escenarios base

Configuración `mon-v1-f63818a0`, referencia `ref-28147cbc`, modelo `mobilenet_v3_large/IMAGENET1K_V2@sha256:5c1a416349c4`,
código en el commit `3a0b5da` (árbol limpio).

| Escenario | Corrida | Inferencias | low / doubt / accepted | low_rate por ventana (10 s) | Alertas |
| --- | --- | --- | --- | --- | --- |
| C1 · normal | `run-c1_normal-20261114T032144` | 120 | 48 / 31 / 41 | 0.25 · 0.30 · 0.30 · 0.50 · 0.50 · 0.55 | ninguna (todas < 0.60) |
| C2 · baja confianza | `run-c2_baja_confianza-20261114T032255` | 120 | 77 / 40 / 3 | 0.70 · 0.65 · 0.35 · 0.85 · 0.85 · 0.45 | `…-001` warning (0.70) y `…-002` critical (0.85), ambas de `baja-confianza-v1` |

El detalle, los comandos y las salidas están en `evidencias/escenarios/` y [`evidencias/indice.md`](evidencias/indice.md).

## Límites conocidos

* Los scores de estos pesos están comprimidos (*label smoothing*): una vista clara rara vez supera 0.8. Por eso los umbrales se
  fijaron con observaciones y no con los valores de ejemplo 0.60/0.80.
* La referencia y C1 se grabaron en la misma sesión y con la misma escena. La referencia no representa otras escenas ni horarios.
* Las ventanas son temporales (reloj de pared). Al reproducir, el número de eventos por ventana puede variar en ±1 según la
  carga del equipo; los scores de cada frame son deterministas, porque la misma secuencia da los mismos scores.
* El PSI con 120 observaciones es ruidoso: C1 da 0.38 frente a la referencia aunque no hay alerta. Solo describe la distribución de salidas.
* CPU y latencias dependen del equipo; G3 se reporta en % de un núcleo del proceso.
* La webcam en vivo no la puede reproducir el evaluador. Por eso las demostraciones usan grabaciones versionadas.
* El portal no tiene autenticación y está pensado para uso local (`127.0.0.1`).
* La confianza no mide calidad ni confirma drift: las alertas indican qué investigar.
