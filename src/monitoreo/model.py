"""Clasificador MobileNetV3-Large preentrenado (ImageNet-1K, torchvision IMAGENET1K_V2).

El artefacto se carga desde ``models/`` y se verifica por SHA-256 antes de usarse, de modo
que cada evento pueda atribuirse a un archivo de pesos concreto (``model_ref``).

Preprocesamiento (idéntico a ``MobileNet_V3_Large_Weights.IMAGENET1K_V2.transforms()``):
BGR (OpenCV) -> RGB -> resize lado corto 232 (bilinear) -> center crop 224 ->
tensor float [0,1] -> normalización mean=(0.485,0.456,0.406) std=(0.229,0.224,0.225).

Salida: softmax sobre 1000 clases. ``score`` = probabilidad top-1 del softmax. Es una
puntuación del modelo, no una probabilidad calibrada de acierto.
"""

from __future__ import annotations

import hashlib
import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_ENUM = MobileNet_V3_Large_Weights.IMAGENET1K_V2
DEFAULT_WEIGHTS = ROOT / "models" / "mobilenet_v3_large-5c1a4163.pth"
# Hash completo del archivo oficial (torchvision publica el prefijo 5c1a4163 en el nombre).
EXPECTED_SHA256 = "5c1a416349c4cf298f2a6a5e2600ed0ee55e604713578f5e74e6bc8bcaef7997"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_weights(dest: Path = DEFAULT_WEIGHTS) -> Path:
    """Descarga los pesos oficiales de torchvision a ``models/`` (solo si faltan)."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    torch.hub.download_url_to_file(WEIGHTS_ENUM.url, str(dest), hash_prefix="5c1a4163")
    return dest


@dataclass
class Prediction:
    class_id: int
    class_name: str
    score: float
    top5: list[dict]
    preprocess_ms: float
    inference_ms: float


class Classifier:
    name = "mobilenet_v3_large"
    weights_tag = "IMAGENET1K_V2"
    task = "classification"

    def __init__(self, weights_path: Path | str = DEFAULT_WEIGHTS, num_threads: int | None = None):
        self.weights_path = Path(weights_path)
        if not self.weights_path.exists():
            raise FileNotFoundError(
                f"No existe {self.weights_path}. Ejecuta: python -m monitoreo.cli fetch-model"
            )
        self.sha256 = sha256_file(self.weights_path)
        if self.sha256 != EXPECTED_SHA256:
            raise ValueError(f"SHA-256 inesperado para los pesos: {self.sha256}")
        if num_threads:
            torch.set_num_threads(num_threads)
        self.net = mobilenet_v3_large(weights=None)
        self.net.load_state_dict(torch.load(self.weights_path, map_location="cpu", weights_only=True))
        self.net.eval()
        self.transform = WEIGHTS_ENUM.transforms()
        self.categories: list[str] = list(WEIGHTS_ENUM.meta["categories"])
        self.device = "cpu"

    @property
    def model_ref(self) -> str:
        return f"{self.name}/{self.weights_tag}@sha256:{self.sha256[:12]}"

    def identity(self) -> dict:
        return {
            "model_ref": self.model_ref,
            "architecture": self.name,
            "weights": f"torchvision MobileNet_V3_Large_Weights.{self.weights_tag}",
            "weights_file": str(self.weights_path.relative_to(ROOT)).replace("\\", "/"),
            "weights_sha256": self.sha256,
            "weights_url": WEIGHTS_ENUM.url,
            "task": self.task,
            "output": "softmax 1000 clases (multiclase, una etiqueta por imagen); score = prob. top-1",
            "num_classes": len(self.categories),
            "preprocessing": {
                "color": "BGR->RGB",
                "resize_short_side": 232,
                "interpolation": "bilinear",
                "center_crop": 224,
                "mean": [0.485, 0.456, 0.406],
                "std": [0.229, 0.224, 0.225],
            },
            "environment": runtime_environment(),
        }

    @torch.inference_mode()
    def predict(self, frame_bgr: np.ndarray, topk: int = 5) -> Prediction:
        t0 = time.perf_counter()
        rgb = Image.fromarray(frame_bgr[:, :, ::-1].copy())
        x = self.transform(rgb).unsqueeze(0)
        t1 = time.perf_counter()
        logits = self.net(x)
        t2 = time.perf_counter()
        probs = torch.softmax(logits, dim=1)[0]
        vals, idx = probs.topk(topk)
        top = [
            {"class_id": int(i), "class_name": self.categories[int(i)], "score": round(float(v), 6)}
            for v, i in zip(vals, idx)
        ]
        return Prediction(
            class_id=top[0]["class_id"],
            class_name=top[0]["class_name"],
            score=top[0]["score"],
            top5=top,
            preprocess_ms=(t1 - t0) * 1000,
            inference_ms=(t2 - t1) * 1000,
        )


def runtime_environment() -> dict:
    import torchvision

    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torchvision": torchvision.__version__,
        "device": "cpu",
        "torch_threads": torch.get_num_threads(),
    }
