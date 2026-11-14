"""Instrumentación Prometheus (prometheus_client) expuesta en ``/metrics`` del portal.

Es una vista en vivo complementaria. Las métricas base se calculan desde SQLite
(``monitoreo.metrics``) para poder recalcularlas a partir de observaciones primarias.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

FRAMES_RECEIVED = Counter("cv_frames_received_total", "Frames recibidos de la fuente", ["source_id"])
FRAMES_DROPPED = Counter("cv_frames_dropped_total", "Frames no inferidos por motivo", ["source_id", "reason"])
RECONNECTS = Counter("cv_source_reconnect_attempts_total", "Intentos de reconexión de la fuente", ["source_id"])
LAST_FRAME_TS = Gauge("cv_last_frame_timestamp_seconds", "Epoch del último frame recibido", ["source_id"])
INFERENCES = Counter("cv_inferences_total", "Inferencias terminadas por estado y zona",
                     ["source_id", "model_ref", "status", "zone"])
INFERENCE_SECONDS = Histogram("cv_inference_seconds", "Duración del forward del modelo (sin preprocesamiento)",
                              buckets=(0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0))
E2E_SECONDS = Histogram("cv_e2e_seconds", "Recepción del frame hasta evento persistido",
                        buckets=(0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0))
SCORE = Histogram("cv_top1_score", "Puntuación top-1 (no es probabilidad de acierto)",
                  buckets=tuple(i / 10 for i in range(1, 11)))
PROCESS_CPU = Gauge("cv_process_cpu_percent", "CPU del proceso (% de un núcleo)")
PROCESS_RSS = Gauge("cv_process_rss_megabytes", "Memoria residente del proceso")
QUEUE_LEN = Gauge("cv_queue_length", "Frames pendientes en la cola de inferencia")
ALERTS_ACTIVE = Gauge("cv_alerts_active", "Alertas activas por regla", ["rule_id"])


def observe_event(ev: dict) -> None:
    INFERENCES.labels(ev["source_id"], ev["model_ref"], ev["status"], ev["zone"] or "none").inc()
    if ev.get("inference_ms") is not None:
        INFERENCE_SECONDS.observe(ev["inference_ms"] / 1000)
    E2E_SECONDS.observe(ev["e2e_ms"] / 1000)
    if ev.get("score") is not None:
        SCORE.observe(ev["score"])


def observe_resources(row: dict) -> None:
    PROCESS_CPU.set(row["process_cpu_pct"])
    PROCESS_RSS.set(row["process_rss_mb"])
    QUEUE_LEN.set(row["queue_len"])
