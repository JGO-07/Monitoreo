"""Carga y validación de la configuración de monitoreo y de las fuentes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .model import ROOT


class ModelCfg(BaseModel):
    weights_file: str
    expected_model_ref: str
    num_threads: int | None = None


class SamplingCfg(BaseModel):
    infer_every_n: int = Field(1, ge=1)
    queue_max: int = Field(8, ge=1)


class ThresholdsCfg(BaseModel):
    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _orden(self):
        if not self.lower < self.upper:
            raise ValueError("Se requiere 0 <= lower < upper <= 1")
        return self


class CaptureCfg(BaseModel):
    low_every_n: int = Field(1, ge=1)
    doubt_every_n: int = Field(1, ge=1)
    accepted_every_n: int = Field(10, ge=1)
    jpeg_quality: int = Field(90, ge=10, le=100)


class ResourcesCfg(BaseModel):
    sample_interval_s: float = Field(1.0, gt=0)


class MonitorConfig(BaseModel):
    config_id: str
    model: ModelCfg
    sampling: SamplingCfg
    thresholds: ThresholdsCfg
    capture: CaptureCfg
    resources: ResourcesCfg = ResourcesCfg()
    # Se completan en hitos posteriores (referencia, ventanas, reglas)
    reference: dict | None = None
    windows: dict | None = None
    alerts: dict | None = None

    # metadatos calculados
    source_path: str = ""
    raw_text: str = ""

    def canonical(self) -> dict:
        return self.model_dump(exclude={"source_path", "raw_text"})

    @property
    def config_ref(self) -> str:
        blob = json.dumps(self.canonical(), sort_keys=True, ensure_ascii=False).encode()
        return f"{self.config_id}-{hashlib.sha256(blob).hexdigest()[:8]}"


class SourceCfg(BaseModel):
    source_id: str
    kind: Literal["sequence", "video", "webcam"]
    path: str | None = None          # carpeta (sequence) o archivo (video)
    device: int = 0                  # webcam
    fps: float = Field(4.0, gt=0)    # frecuencia de reproducción / captura declarada
    loop: bool = False
    max_frames: int | None = None
    duration_s: float | None = None
    reconnect_attempts: int = 3
    reconnect_backoff_s: float = 1.0


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_config(path: str | Path = "config/monitoreo.yaml") -> MonitorConfig:
    p = resolve(path)
    text = p.read_text(encoding="utf-8")
    cfg = MonitorConfig(**yaml.safe_load(text))
    cfg.source_path = str(p.relative_to(ROOT)).replace("\\", "/") if p.is_relative_to(ROOT) else str(p)
    cfg.raw_text = text
    return cfg
