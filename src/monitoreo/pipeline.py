"""Pipeline de monitoreo: fuente -> cola -> inferencia -> evento/captura -> reglas.

Hilos:
  * productor: lee la fuente al ritmo declarado y aplica la política de muestreo;
  * consumidor: ejecuta el modelo, clasifica la zona, guarda captura y evento;
  * muestreador de recursos: CPU/RAM/cola/disco cada ``resources.sample_interval_s``;
  * evaluador de reglas (si se configura): cierra ventanas temporales y emite alertas.
"""

from __future__ import annotations

import hashlib
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import psutil

from . import prom
from .config import MonitorConfig, SourceCfg, resolve
from .model import ROOT, Classifier
from .policy import zone_for
from .sources import RawFrame, open_source
from .store import Store, dumps


def git_state() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "config", "models"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip() != ""
        return commit, dirty
    except Exception:
        return None, None


def new_run_id(scenario: str) -> str:
    return f"run-{scenario}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"


class Pipeline:
    def __init__(self, cfg: MonitorConfig, source: SourceCfg, store: Store, scenario: str = "adhoc",
                 classifier: Classifier | None = None, gaps: dict[int, float] | None = None,
                 rule_engine_factory: Callable | None = None, notes: str | None = None,
                 run_id: str | None = None):
        self.cfg, self.source, self.store, self.scenario = cfg, source, store, scenario
        self.clf = classifier or Classifier(resolve(cfg.model.weights_file), cfg.model.num_threads)
        self.gaps = gaps or {}
        self.notes = notes
        self.run_id = run_id or new_run_id(scenario)
        self.q: queue.Queue = queue.Queue(maxsize=cfg.sampling.queue_max)
        self._stop = threading.Event()
        self._producer_done = threading.Event()
        self._zone_counters = {"low": 0, "doubt": 0, "accepted": 0}
        self.latest: dict | None = None            # último frame inferido (para vista en vivo)
        self.rules = rule_engine_factory(self) if rule_engine_factory else None
        self.threads: list[threading.Thread] = []
        self.error: BaseException | None = None

    # ---- ciclo de vida ----------------------------------------------------------
    def _register(self) -> None:
        cfg = self.cfg
        self.store.upsert("configs", {
            "config_ref": cfg.config_ref, "config_id": cfg.config_id, "source_path": cfg.source_path,
            "created_at": time.time(), "canonical_json": dumps(cfg.canonical()), "raw_yaml": cfg.raw_text,
        })
        commit, dirty = git_state()
        self.store.insert("runs", {
            "run_id": self.run_id, "scenario": self.scenario, "started_at": time.time(),
            "config_ref": cfg.config_ref, "model_ref": self.clf.model_ref, "model_json": dumps(self.clf.identity()),
            "source_id": self.source.source_id, "source_json": dumps(self.source.model_dump()),
            "git_commit": commit, "git_dirty": None if dirty is None else int(dirty), "notes": self.notes,
        })

    def start(self) -> None:
        self._register()
        targets = [self._produce, self._consume, self._sample_resources]
        if self.rules:
            targets.append(self._tick_rules)
        for t in targets:
            th = threading.Thread(target=self._guard(t), name=t.__name__, daemon=True)
            th.start()
            self.threads.append(th)

    def _guard(self, fn):
        def wrapper():
            try:
                fn()
            except BaseException as e:  # se registra y detiene el pipeline
                self.error = e
                self.store.insert("source_events", {"run_id": self.run_id, "timestamp": time.time(),
                                                    "kind": "pipeline_error", "detail_json": dumps({"error": repr(e)})})
                self._stop.set()
                self._producer_done.set()
        return wrapper

    def stop(self) -> None:
        self._stopped_by_user = True
        self._stop.set()

    def wait(self) -> None:
        for th in self.threads:
            th.join()
        if self.rules:
            self.rules.finalize(time.time())
        status = "failed" if self.error else ("stopped" if getattr(self, "_stopped_by_user", False)
                                              else "completed")
        self.store.execute("UPDATE runs SET ended_at=?, status=? WHERE run_id=?", (time.time(), status, self.run_id))

    def run(self) -> str:
        self.start()
        self.wait()
        if self.error:
            raise self.error
        return self.run_id

    # ---- hilos --------------------------------------------------------------------
    def _source_event(self, kind: str, detail: dict) -> None:
        self.store.insert("source_events", {"run_id": self.run_id, "timestamp": time.time(), "kind": kind,
                                            "detail_json": dumps(detail)})
        if kind == "reconnect_attempt":
            prom.RECONNECTS.labels(self.source.source_id).inc()

    def _frame_id(self, seq: int) -> str:
        return f"{self.run_id}-f{seq:06d}"

    def _produce(self) -> None:
        n = self.cfg.sampling.infer_every_n
        sid = self.source.source_id
        try:
            for raw in open_source(self.source, self._source_event, self._stop.is_set, self.gaps):
                prom.FRAMES_RECEIVED.labels(sid).inc()
                prom.LAST_FRAME_TS.labels(sid).set(raw.received_at)
                row = {"frame_id": self._frame_id(raw.seq), "run_id": self.run_id, "seq": raw.seq,
                       "received_at": raw.received_at, "origin": raw.origin, "event_id": None}
                if raw.error:
                    outcome = "read_error"
                elif raw.seq % n != 0:
                    outcome = "dropped_sampling"
                else:
                    try:
                        self.q.put_nowait((raw, time.time()))
                        outcome = "queued"
                    except queue.Full:
                        outcome = "dropped_saturation"
                row["outcome"] = outcome
                self.store.insert("frames", row)
                if outcome not in ("queued",):
                    prom.FRAMES_DROPPED.labels(sid, outcome).inc()
        finally:
            self._producer_done.set()

    def _consume(self) -> None:
        while True:
            try:
                raw, enq = self.q.get(timeout=0.2)
            except queue.Empty:
                if self._producer_done.is_set():
                    return
                continue
            self._process(raw, enq)

    def _process(self, raw: RawFrame, enqueued_at: float) -> None:
        cfg, th = self.cfg, self.cfg.thresholds
        frame_id = self._frame_id(raw.seq)
        event_id = f"evt-{self.run_id[4:]}-{raw.seq:06d}"
        dequeued = time.time()
        status, err, pred = "ok", None, None
        try:
            pred = self.clf.predict(raw.image)
        except Exception as e:  # inferencia fallida: estado aparte, sin zona
            status, err = "error", repr(e)
        done = time.time()
        score = pred.score if pred else None
        if pred is not None and score is None:
            status = "no_score"
        zone = zone_for(score, th.lower, th.upper) if status == "ok" else None

        image_ref = image_sha = None
        if zone is not None and self._should_capture(zone):
            image_ref, image_sha = self._save_capture(raw, frame_id)

        ev = {
            "event_id": event_id, "run_id": self.run_id, "frame_id": frame_id, "seq": raw.seq,
            "timestamp": raw.received_at, "source_id": self.source.source_id, "task": self.clf.task,
            "model_ref": self.clf.model_ref, "config_ref": cfg.config_ref, "status": status,
            "class_id": pred.class_id if pred else None, "class_name": pred.class_name if pred else None,
            "score": score, "zone": zone, "top5_json": dumps(pred.top5) if pred else None,
            "lower_threshold": th.lower, "upper_threshold": th.upper,
            "image_ref": image_ref, "image_sha256": image_sha,
            "enqueued_at": enqueued_at, "dequeued_at": dequeued, "done_at": done,
            "queue_ms": (dequeued - enqueued_at) * 1000,
            "preprocess_ms": pred.preprocess_ms if pred else None,
            "inference_ms": pred.inference_ms if pred else None,
            "e2e_ms": (done - raw.received_at) * 1000, "error": err,
        }
        self.store.insert("events", ev)
        self.store.execute("UPDATE frames SET outcome='inferred', event_id=? WHERE frame_id=?", (event_id, frame_id))
        prom.observe_event(ev)
        if status == "ok":
            ok, jpg = cv2.imencode(".jpg", raw.image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            self.latest = {"event": ev, "jpeg": jpg.tobytes() if ok else None}
        if self.rules:
            self.rules.on_event(ev)

    def _should_capture(self, zone: str) -> bool:
        every = {"low": self.cfg.capture.low_every_n, "doubt": self.cfg.capture.doubt_every_n,
                 "accepted": self.cfg.capture.accepted_every_n}[zone]
        c = self._zone_counters[zone]
        self._zone_counters[zone] += 1
        return c % every == 0

    def _save_capture(self, raw: RawFrame, frame_id: str) -> tuple[str, str]:
        rel = Path("capturas") / self.run_id / f"{frame_id}.jpg"
        dest = self.store.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", raw.image, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.capture.jpeg_quality])
        if not ok:
            raise RuntimeError("No se pudo codificar la captura")
        data = buf.tobytes()
        dest.write_bytes(data)
        return rel.as_posix(), hashlib.sha256(data).hexdigest()

    def _sample_resources(self) -> None:
        proc = psutil.Process()
        proc.cpu_percent(None)
        psutil.cpu_percent(None)
        interval = self.cfg.resources.sample_interval_s
        while not (self._producer_done.is_set() and self.q.empty()):
            time.sleep(interval)
            row = {
                "run_id": self.run_id, "timestamp": time.time(),
                "process_cpu_pct": proc.cpu_percent(None),
                "process_rss_mb": proc.memory_info().rss / 2**20,
                "system_cpu_pct": psutil.cpu_percent(None),
                "system_ram_pct": psutil.virtual_memory().percent,
                "queue_len": self.q.qsize(),
                "disk_free_gb": psutil.disk_usage(str(self.store.root)).free / 2**30,
            }
            self.store.insert("resource_samples", row)
            prom.observe_resources(row)

    def _tick_rules(self) -> None:
        while not (self._producer_done.is_set() and self.q.empty()):
            time.sleep(0.5)
            self.rules.tick(time.time())
