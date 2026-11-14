# Proyecto 6 · Centro de monitoreo de computer vision

Portal individual que convierte un stream visual en inferencias reales de **clasificación**, eventos con
capturas vinculadas, métricas por grupo (G1–G7) y alertas investigables.

> Estado: **en construcción** (hito 1: modelo e inferencia real). Este README se completa en cada hito.

## Decisiones de alcance

| Tema | Decisión |
| --- | --- |
| Tarea | Clasificación de imágenes (multiclase, una etiqueta por imagen). |
| Modelo | MobileNetV3-Large preentrenado en ImageNet-1K, pesos `torchvision IMAGENET1K_V2` (sin entrenamiento propio). |
| Fuente | Reproducción de una secuencia/video versionado a frecuencia declarada (principal y reproducible) y webcam opcional. |
| Ejecución | Local, CPU. |
| Stack | Python · FastAPI · SQLite · portal HTML (Jinja) · prometheus_client. |

## Modelo

| Campo | Valor |
| --- | --- |
| Artefacto | [`models/mobilenet_v3_large-5c1a4163.pth`](models/) (22 132 113 bytes) |
| SHA-256 | `5c1a416349c4cf298f2a6a5e2600ed0ee55e604713578f5e74e6bc8bcaef7997` (se verifica al cargar) |
| Origen | <https://download.pytorch.org/models/mobilenet_v3_large-5c1a4163.pth> |
| `model_ref` en eventos | `mobilenet_v3_large/IMAGENET1K_V2@sha256:5c1a416349c4` |
| Clases | 1000 categorías ImageNet-1K (`MobileNet_V3_Large_Weights.IMAGENET1K_V2.meta["categories"]`) |
| Preprocesamiento | BGR→RGB, resize del lado corto a 232 (bilinear), center crop 224, normalización mean (0.485, 0.456, 0.406) / std (0.229, 0.224, 0.225) |
| Salida | Softmax de 1000 clases; `score` = probabilidad top-1. **Es una puntuación del modelo, no una probabilidad calibrada de acierto.** |

El manifiesto completo, con el entorno de ejecución, está en
[`evidencias/modelo/manifiesto_modelo.json`](evidencias/modelo/manifiesto_modelo.json).

## Instalación

```bash
python -m venv .venv
.venv/Scripts/pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/pip install -r requirements.lock.txt
.venv/Scripts/pip install -e .
```

En Linux/macOS, usa `.venv/bin/` en vez de `.venv/Scripts/`.

## Primera inferencia real

```bash
.venv/Scripts/python -m monitoreo.cli fetch-model        # solo si falta el .pth; verifica el hash
.venv/Scripts/python -m monitoreo.cli model-info
.venv/Scripts/python -m monitoreo.cli infer --image data/muestras/coffee.jpg
.venv/Scripts/python -m pytest -q
```

Las salidas de referencia están en [`evidencias/modelo/`](evidencias/modelo/). Las imágenes de muestra
`data/muestras/{chelsea,coffee}.jpg` vienen de `skimage.data`, con licencia BSD-3 de scikit-image.
