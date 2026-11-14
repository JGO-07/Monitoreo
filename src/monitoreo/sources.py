"""Fuentes visuales reproducidas como stream a una frecuencia declarada.

- ``sequence``: carpeta de imágenes (orden lexicográfico); una imagen por intervalo 1/fps.
- ``video``: archivo de video; se entregan frames a ``fps`` declarado (se saltan frames del
  archivo si su FPS nativo es mayor, para respetar el tiempo real del contenido).
- ``webcam``: cámara local vía OpenCV, limitada a ``fps``.

Cada frame recibido se entrega como :class:`RawFrame` con número de secuencia y marca de tiempo
de recepción. Un fallo de lectura se entrega con ``image=None`` y ``error`` para que el pipeline
lo registre (no se oculta). Las reconexiones se notifican mediante ``on_event``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np

from .config import SourceCfg, resolve

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class RawFrame:
    seq: int
    received_at: float           # epoch (s), reloj de pared del proceso
    image: np.ndarray | None
    origin: str                  # archivo o índice de frame de origen
    error: str | None = None
    meta: dict = field(default_factory=dict)


EventCb = Callable[[str, dict], None]


class Pacer:
    """Mantiene un ritmo fijo 1/fps con un reloj monótono (sin acumular deriva)."""

    def __init__(self, fps: float):
        self.period = 1.0 / fps
        self.next_t = time.monotonic()

    def wait(self, stop: Callable[[], bool]) -> None:
        self.next_t += self.period
        while not stop():
            dt = self.next_t - time.monotonic()
            if dt <= 0:
                return
            time.sleep(min(dt, 0.05))

    def pause(self, seconds: float, stop: Callable[[], bool]) -> None:
        end = time.monotonic() + seconds
        while not stop() and time.monotonic() < end:
            time.sleep(0.05)
        self.next_t = time.monotonic()


def open_source(cfg: SourceCfg, on_event: EventCb | None = None,
                stop: Callable[[], bool] = lambda: False,
                gaps: dict[int, float] | None = None) -> Iterator[RawFrame]:
    """Devuelve un iterador de frames. ``gaps`` = {seq: segundos} simula interrupciones."""
    on_event = on_event or (lambda kind, detail: None)
    gaps = gaps or {}
    if cfg.kind == "sequence":
        return _sequence(cfg, on_event, stop, gaps)
    if cfg.kind == "video":
        return _video(cfg, on_event, stop, gaps)
    return _webcam(cfg, on_event, stop, gaps)


def _limits(cfg: SourceCfg, seq: int, t0: float) -> bool:
    if cfg.max_frames is not None and seq >= cfg.max_frames:
        return True
    if cfg.duration_s is not None and time.monotonic() - t0 >= cfg.duration_s:
        return True
    return False


def _sequence(cfg, on_event, stop, gaps):
    folder = resolve(cfg.path)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)
    if not files:
        raise FileNotFoundError(f"La carpeta {folder} no contiene imágenes")
    on_event("source_opened", {"kind": "sequence", "path": cfg.path, "files": len(files), "fps": cfg.fps})
    pacer, seq, t0 = Pacer(cfg.fps), 0, time.monotonic()
    while not stop():
        for f in files:
            if stop() or _limits(cfg, seq, t0):
                on_event("source_closed", {"frames": seq})
                return
            if seq in gaps:
                on_event("source_gap", {"seq": seq, "seconds": gaps[seq]})
                pacer.pause(gaps[seq], stop)
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            err = None if img is not None else "decode_error"
            yield RawFrame(seq, time.time(), img, f.name, err)
            seq += 1
            pacer.wait(stop)
        if not cfg.loop:
            break
    on_event("source_closed", {"frames": seq})


def _video(cfg, on_event, stop, gaps):
    path = str(resolve(cfg.path))
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"No se pudo abrir el video {path}")
    native = cap.get(cv2.CAP_PROP_FPS) or cfg.fps
    step = max(1, round(native / cfg.fps))
    on_event("source_opened", {"kind": "video", "path": cfg.path, "native_fps": native, "fps": cfg.fps, "step": step})
    pacer, seq, idx, t0 = Pacer(cfg.fps), 0, 0, time.monotonic()
    while not stop() and not _limits(cfg, seq, t0):
        ok, img = cap.read()
        if not ok:
            if cfg.loop:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                idx = 0
                continue
            break
        if idx % step == 0:
            if seq in gaps:
                on_event("source_gap", {"seq": seq, "seconds": gaps[seq]})
                pacer.pause(gaps[seq], stop)
            yield RawFrame(seq, time.time(), img, f"frame{idx}")
            seq += 1
            pacer.wait(stop)
        idx += 1
    cap.release()
    on_event("source_closed", {"frames": seq})


def _webcam(cfg, on_event, stop, gaps):
    def _open():
        c = cv2.VideoCapture(cfg.device, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(cfg.device)
        return c if c.isOpened() else None

    cap = _open()
    if cap is None:
        raise RuntimeError(f"No se pudo abrir la webcam {cfg.device}")
    on_event("source_opened", {"kind": "webcam", "device": cfg.device, "fps": cfg.fps})
    pacer, seq, t0 = Pacer(cfg.fps), 0, time.monotonic()
    while not stop() and not _limits(cfg, seq, t0):
        ok, img = cap.read()
        if not ok or img is None:
            yield RawFrame(seq, time.time(), None, f"cam{cfg.device}", "read_error")
            seq += 1
            cap.release()
            cap = None
            for attempt in range(1, cfg.reconnect_attempts + 1):
                on_event("reconnect_attempt", {"attempt": attempt})
                pacer.pause(cfg.reconnect_backoff_s * attempt, stop)
                cap = _open()
                if cap is not None:
                    on_event("reconnected", {"attempt": attempt})
                    break
            if cap is None:
                on_event("source_lost", {"attempts": cfg.reconnect_attempts})
                break
            continue
        yield RawFrame(seq, time.time(), img, f"cam{cfg.device}")
        seq += 1
        pacer.wait(stop)
    if cap is not None:
        cap.release()
    on_event("source_closed", {"frames": seq})
