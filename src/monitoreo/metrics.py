"""Métricas por grupo (G1–G7) calculadas desde observaciones primarias en SQLite.

Cada métrica declara su definición (qué mide, unidad, fórmula, población, ventana,
exclusiones, tratamiento de cero observaciones, fuente e instrumentación) y una función de
cálculo que devuelve el valor, el número de observaciones, la consulta usada y los IDs
primarios que lo respaldan. ``scripts/recalcular_metricas.py`` recalcula las métricas base
desde la exportación CSV/JSONL con otro código (solo biblioteca estándar).

Percentiles: interpolación lineal sobre los datos ordenados, ``h = (n-1)·p`` (equivalente a
``numpy.quantile`` por defecto).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .store import Store


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    h = (len(xs) - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    return xs[lo] + (xs[hi] - xs[lo]) * (h - lo)


@dataclass
class Metric:
    id: str
    group: str
    base: bool
    name: str
    unit: str
    definition: str
    formula: str
    population: str
    window: str
    exclusions: str
    zero_obs: str
    instrumentation: str
    compute: Callable[[Store, str, dict], dict] = field(repr=False)

    def spec(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "compute"}


# ---------------------------------------------------------------------------------------------
# Funciones de cálculo
# ---------------------------------------------------------------------------------------------
def _ok_events(store: Store, run_id: str, cols: str = "*") -> list[dict]:
    return store.query(f"SELECT {cols} FROM events WHERE run_id=? AND status='ok' ORDER BY seq", (run_id,))


def g1_inference_time(store, run_id, ctx):
    q = "SELECT event_id, inference_ms FROM events WHERE run_id=? AND status='ok' AND inference_ms IS NOT NULL"
    rows = store.query(q, (run_id,))
    vals = [r["inference_ms"] for r in rows]
    p95 = percentile(vals, 0.95)
    worst = sorted(rows, key=lambda r: -r["inference_ms"])[:3]
    return {"value": p95, "n": len(vals), "p50": percentile(vals, 0.50), "max": max(vals) if vals else None,
            "query": q, "evidence_ids": [r["event_id"] for r in worst],
            "status": "ok" if vals else "sin_observaciones"}


def g1_throughput(store, run_id, ctx):
    q = "SELECT event_id, done_at FROM events WHERE run_id=? AND status='ok' ORDER BY done_at"
    rows = store.query(q, (run_id,))
    if len(rows) < 2:
        return {"value": None, "n": len(rows), "query": q, "status": "sin_observaciones"}
    span = rows[-1]["done_at"] - rows[0]["done_at"]
    return {"value": (len(rows) - 1) / span if span > 0 else None, "n": len(rows), "span_s": span, "query": q,
            "evidence_ids": [rows[0]["event_id"], rows[-1]["event_id"]], "status": "ok"}


def g1_error_rate(store, run_id, ctx):
    q = "SELECT status, COUNT(*) n FROM events WHERE run_id=? GROUP BY status"
    c = {r["status"]: r["n"] for r in store.query(q, (run_id,))}
    total = sum(c.values())
    return {"value": c.get("error", 0) / total if total else None, "n": total, "by_status": c, "query": q,
            "status": "ok" if total else "sin_observaciones"}


def g2_capture_fps(store, run_id, ctx):
    q = "SELECT frame_id, received_at FROM frames WHERE run_id=? AND outcome != 'read_error' ORDER BY seq"
    rows = store.query(q, (run_id,))
    if len(rows) < 2:
        return {"value": None, "n": len(rows), "query": q, "status": "sin_observaciones"}
    span = rows[-1]["received_at"] - rows[0]["received_at"]
    gaps = [b["received_at"] - a["received_at"] for a, b in zip(rows, rows[1:])]
    return {"value": (len(rows) - 1) / span if span > 0 else None, "n": len(rows), "span_s": span,
            "declared_fps": ctx.get("declared_fps"), "max_interval_s": max(gaps),
            "query": q, "evidence_ids": [rows[0]["frame_id"], rows[-1]["frame_id"]], "status": "ok"}


def g2_dropped(store, run_id, ctx):
    q = "SELECT outcome, COUNT(*) n FROM frames WHERE run_id=? GROUP BY outcome"
    c = {r["outcome"]: r["n"] for r in store.query(q, (run_id,))}
    total = sum(c.values())
    if not total:
        return {"value": None, "n": 0, "query": q, "status": "sin_observaciones"}
    return {"value": (c.get("dropped_sampling", 0) + c.get("dropped_saturation", 0)) / total, "n": total,
            "by_reason": {k: {"n": v, "rate": v / total} for k, v in c.items()}, "query": q, "status": "ok"}


def g2_e2e_latency(store, run_id, ctx):
    q = "SELECT event_id, e2e_ms FROM events WHERE run_id=? AND status='ok'"
    rows = store.query(q, (run_id,))
    vals = [r["e2e_ms"] for r in rows]
    return {"value": percentile(vals, 0.95), "n": len(vals), "p50": percentile(vals, 0.5), "query": q,
            "evidence_ids": [r["event_id"] for r in sorted(rows, key=lambda r: -r["e2e_ms"])[:3]],
            "status": "ok" if vals else "sin_observaciones"}


def g3_process_cpu(store, run_id, ctx):
    q = "SELECT id, timestamp, process_cpu_pct FROM resource_samples WHERE run_id=? ORDER BY timestamp"
    rows = store.query(q, (run_id,))
    vals = [r["process_cpu_pct"] for r in rows if r["process_cpu_pct"] is not None]
    return {"value": sum(vals) / len(vals) if vals else None, "n": len(vals), "max": max(vals) if vals else None,
            "query": q, "evidence_ids": [r["id"] for r in rows[:1] + rows[-1:]],
            "status": "ok" if vals else "sin_observaciones"}


def g3_process_rss(store, run_id, ctx):
    q = "SELECT process_rss_mb FROM resource_samples WHERE run_id=?"
    vals = [r["process_rss_mb"] for r in store.query(q, (run_id,))]
    return {"value": max(vals) if vals else None, "n": len(vals), "mean": sum(vals) / len(vals) if vals else None,
            "query": q, "status": "ok" if vals else "sin_observaciones"}


def g4_low_rate(store, run_id, ctx):
    q = "SELECT event_id, score, zone FROM events WHERE run_id=? AND status='ok' AND score IS NOT NULL"
    rows = store.query(q, (run_id,))
    n = len(rows)
    low = [r for r in rows if r["zone"] == "low"]
    zones = Counter(r["zone"] for r in rows)
    return {"value": len(low) / n if n else None, "n": n, "n_low": len(low), "by_zone": dict(zones),
            "reference_low_rate": (ctx.get("reference") or {}).get("low_rate"),
            "query": q, "evidence_ids": [r["event_id"] for r in low[:5]],
            "status": "ok" if n else "sin_observaciones"}


def g4_by_class(store, run_id, ctx):
    q = "SELECT class_name, COUNT(*) n FROM events WHERE run_id=? AND status='ok' GROUP BY class_name ORDER BY n DESC"
    rows = store.query(q, (run_id,))
    total = sum(r["n"] for r in rows)
    return {"value": len(rows), "n": total,
            "distribution": [{"class_name": r["class_name"], "n": r["n"], "share": r["n"] / total} for r in rows[:15]],
            "query": q, "status": "ok" if total else "sin_observaciones"}


def g4_psi_vs_reference(store, run_id, ctx):
    """PSI entre el histograma de scores de la corrida y el de la referencia (10 bins de 0.1)."""
    ref = ctx.get("reference")
    q = "SELECT score FROM events WHERE run_id=? AND status='ok' AND score IS NOT NULL"
    vals = [r["score"] for r in store.query(q, (run_id,))]
    if not ref or not vals:
        return {"value": None, "n": len(vals), "query": q,
                "status": "sin_referencia" if not ref else "sin_observaciones"}
    eps = 1e-4
    cur = [0] * 10
    for v in vals:
        cur[min(int(v * 10), 9)] += 1
    rc = ref["histogram"]["counts"]
    rt, ct = sum(rc), len(vals)
    psi = 0.0
    bins = []
    for i in range(10):
        pr, pc = max(rc[i] / rt, eps), max(cur[i] / ct, eps)
        psi += (pc - pr) * math.log(pc / pr)
        bins.append({"bin": f"[{i / 10:.1f},{(i + 1) / 10:.1f})", "ref": rc[i] / rt, "run": cur[i] / ct})
    return {"value": psi, "n": ct, "reference_n": rt, "bins": bins, "query": q, "status": "ok",
            "interpretation": "PSI < 0.1 estable; 0.1–0.25 cambio moderado; > 0.25 cambio importante "
                              "(convención usual; describe la distribución de salidas, no calidad)."}


REQUIRED_OK = ("class_id", "class_name", "score", "zone", "top5_json")
REQUIRED_ALL = ("event_id", "run_id", "frame_id", "timestamp", "source_id", "task", "model_ref", "config_ref",
                "status", "lower_threshold", "upper_threshold")


def g5_traceability(store, run_id, ctx):
    import hashlib

    q = ("SELECT e.*, f.event_id AS frame_event_id, c.config_ref AS cfg_exists FROM events e "
         "LEFT JOIN frames f ON f.frame_id=e.frame_id LEFT JOIN configs c ON c.config_ref=e.config_ref "
         "WHERE e.run_id=?")
    rows = store.query(q, (run_id,))
    complete, incomplete = 0, []
    for r in rows:
        problems = [k for k in REQUIRED_ALL if r[k] is None]
        if r["status"] == "ok":
            problems += [k for k in REQUIRED_OK if r[k] is None]
        if r["frame_event_id"] != r["event_id"]:
            problems.append("frame_link")
        if r["cfg_exists"] is None:
            problems.append("config_link")
        if r["image_ref"]:
            p = store.image_path(r["image_ref"])
            if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != r["image_sha256"]:
                problems.append("image_ref")
        if problems:
            incomplete.append({"event_id": r["event_id"], "problems": problems})
        else:
            complete += 1
    n = len(rows)
    return {"value": complete / n if n else None, "n": n, "complete": complete, "incomplete": incomplete[:20],
            "query": q, "status": "ok" if n else "sin_observaciones"}


def g5_linked_images(store, run_id, ctx):
    import hashlib

    q = "SELECT event_id, zone, image_ref, image_sha256 FROM events WHERE run_id=? AND image_ref IS NOT NULL"
    rows = store.query(q, (run_id,))
    ok = [r for r in rows if store.image_path(r["image_ref"]).exists()
          and hashlib.sha256(store.image_path(r["image_ref"]).read_bytes()).hexdigest() == r["image_sha256"]]
    return {"value": len(ok) / len(rows) if rows else None, "n": len(rows), "valid": len(ok),
            "by_zone": dict(Counter(r["zone"] for r in rows)), "query": q,
            "evidence_ids": [r["event_id"] for r in rows[:5]], "status": "ok" if rows else "sin_observaciones"}


def g5_invalid_inputs(store, run_id, ctx):
    q = "SELECT outcome, COUNT(*) n FROM frames WHERE run_id=? GROUP BY outcome"
    c = {r["outcome"]: r["n"] for r in store.query(q, (run_id,))}
    total = sum(c.values())
    return {"value": c.get("read_error", 0) / total if total else None, "n": total, "invalid": c.get("read_error", 0),
            "query": q, "status": "ok" if total else "sin_observaciones"}


def g6_version_match(store, run_id, ctx):
    q = "SELECT model_ref, COUNT(*) n FROM events WHERE run_id=? GROUP BY model_ref"
    rows = store.query(q, (run_id,))
    total = sum(r["n"] for r in rows)
    exp = ctx["expected_model_ref"]
    match = sum(r["n"] for r in rows if r["model_ref"] == exp)
    return {"value": match / total if total else None, "n": total, "expected_model_ref": exp,
            "reported": {r["model_ref"]: r["n"] for r in rows}, "query": q,
            "status": "ok" if total else "sin_observaciones"}


def g6_last_report_age(store, run_id, ctx):
    q = ("SELECT source_id, MAX(timestamp) last_ts, COUNT(*) n FROM events WHERE run_id=? GROUP BY source_id")
    rows = store.query(q, (run_id,))
    run = store.one("SELECT ended_at FROM runs WHERE run_id=?", (run_id,))
    ref_t = run["ended_at"] if run and run["ended_at"] else None
    per = [{"source_id": r["source_id"], "n": r["n"],
            "age_s_at_run_end": (ref_t - r["last_ts"]) if ref_t else None} for r in rows]
    return {"value": max((p["age_s_at_run_end"] for p in per if p["age_s_at_run_end"] is not None), default=None),
            "n": len(per), "per_source": per, "query": q, "status": "ok" if per else "sin_observaciones"}


def g7_active_alerts(store, run_id, ctx):
    q = "SELECT alert_id, rule_id, severity, timestamp, resolved_at, status FROM alerts WHERE run_id=? ORDER BY timestamp"
    rows = store.query(q, (run_id,))
    run = store.one("SELECT ended_at FROM runs WHERE run_id=?", (run_id,))
    t_end = run["ended_at"] if run else None
    active_end = [r["alert_id"] for r in rows if r["resolved_at"] is None or (t_end and r["resolved_at"] > t_end)]
    # máximo simultáneo: barrido de inicios/fin
    pts = sorted([(r["timestamp"], 1) for r in rows] + [(r["resolved_at"], -1) for r in rows if r["resolved_at"]])
    cur = peak = 0
    for _, d in pts:
        cur += d
        peak = max(peak, cur)
    return {"value": len(active_end), "n": len(rows), "fired": len(rows), "max_simultaneous": peak,
            "active_alert_ids": active_end, "by_rule": dict(Counter(r["rule_id"] for r in rows)),
            "query": q, "status": "ok"}


def g7_detection_time(store, run_id, ctx):
    """Tiempo desde el inicio conocido de la condición (manifiesto del escenario) hasta la primera alerta."""
    onset = ctx.get("condition_onset_s")
    rule = ctx.get("condition_rule", "baja-confianza-v1")
    run = store.one("SELECT started_at FROM runs WHERE run_id=?", (run_id,))
    q = "SELECT alert_id, timestamp FROM alerts WHERE run_id=? AND rule_id=? ORDER BY timestamp LIMIT 1"
    if onset is None:
        return {"value": None, "n": 0, "query": q, "status": "sin_condicion_declarada"}
    a = store.one(q, (run_id, rule))
    if a is None:
        return {"value": None, "n": 0, "query": q, "status": "sin_alerta"}
    return {"value": a["timestamp"] - (run["started_at"] + onset), "n": 1, "rule_id": rule,
            "condition_onset_s": onset, "evidence_ids": [a["alert_id"]], "query": q, "status": "ok"}


# ---------------------------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------------------------
M = Metric
REGISTRY: list[Metric] = [
    M("G1-tiempo-inferencia-p95", "G1", True, "Tiempo de inferencia p95", "ms",
      "Duración del forward del modelo por frame, separada de captura, cola, preprocesamiento y persistencia.",
      "percentil 95 (interpolación lineal) de events.inference_ms", "eventos status=ok de la corrida",
      "corrida completa", "eventos con status error/no_score (sin forward completo)",
      "sin eventos ok → value=null, status=sin_observaciones",
      "time.perf_counter() alrededor de net(x) en Classifier.predict; también histograma Prometheus cv_inference_seconds",
      g1_inference_time),
    M("G1-throughput", "G1", False, "Throughput de inferencias", "inferencias/s",
      "Inferencias terminadas por segundo.", "(n-1) / (done_at_último - done_at_primero)",
      "eventos status=ok", "corrida completa", "eventos fallidos", "n<2 → null", "events.done_at (time.time())",
      g1_throughput),
    M("G1-tasa-errores", "G1", False, "Tasa de errores de inferencia", "proporción",
      "Fallos técnicos de inferencia entre intentos.", "n(status=error) / n(eventos)", "todos los eventos",
      "corrida completa", "frames no inferidos (no son intentos)", "n=0 → null", "excepción capturada en Pipeline._process",
      g1_error_rate),
    M("G2-fps-captura", "G2", True, "FPS de captura", "frames/s",
      "Frames recibidos por segundo desde la fuente, antes del muestreo (incluye los descartados por muestreo).",
      "(n-1) / (received_at_último - received_at_primero)", "frames con outcome != read_error",
      "corrida completa", "frames con error de lectura", "n<2 → null",
      "frames.received_at (time.time() al entregar el frame la fuente); contador cv_frames_received_total",
      g2_capture_fps),
    M("G2-frames-descartados", "G2", False, "Frames descartados por la aplicación", "proporción",
      "Frames recibidos que no se infirieron, separados por motivo (muestreo, saturación).",
      "n(dropped_sampling + dropped_saturation) / n(frames)", "todos los frames de la corrida", "corrida completa",
      "ninguna; read_error se reporta aparte", "n=0 → null", "frames.outcome; contador cv_frames_dropped_total",
      g2_dropped),
    M("G2-latencia-e2e-p95", "G2", False, "Latencia de extremo a extremo p95", "ms",
      "Desde la recepción del frame hasta el evento terminado (incluye cola, preprocesamiento e inferencia). "
      "Mismo reloj de proceso en ambos extremos.", "p95 de events.e2e_ms", "eventos ok", "corrida completa",
      "eventos fallidos", "null", "time.time() en recepción y al terminar", g2_e2e_latency),
    M("G3-cpu-proceso", "G3", True, "CPU del proceso (media)", "% de un núcleo",
      "Uso de CPU del proceso de monitoreo (puede superar 100 % con varios hilos).",
      "media de resource_samples.process_cpu_pct", "muestras de la corrida (1 por segundo)", "corrida completa",
      "primera lectura de psutil (cebado) descartada", "sin muestras → null",
      "psutil.Process().cpu_percent(None) cada resources.sample_interval_s", g3_process_cpu),
    M("G3-ram-proceso", "G3", False, "RAM del proceso (máximo)", "MB",
      "Memoria residente máxima del proceso.", "max(resource_samples.process_rss_mb)", "muestras de la corrida",
      "corrida completa", "ninguna", "null", "psutil.Process().memory_info().rss", g3_process_rss),
    M("G4-proporcion-baja-confianza", "G4", True, "Proporción de baja confianza", "proporción",
      "Fracción de inferencias con score menor al umbral inferior. Describe salidas, no aciertos.",
      "n(score < lower) / n(status=ok con score)", "eventos status=ok con score (unidad: frame inferido)",
      "corrida completa (por ventana de 10 s en window_evaluations)", "error/no_score",
      "n=0 → null (evidencia insuficiente, no 0)", "events.zone calculada por policy.zone_for", g4_low_rate),
    M("G4-predicciones-por-clase", "G4", False, "Predicciones por clase", "conteo por clase",
      "Distribución de la clase top-1 (unidad: frame inferido).", "conteo por class_name", "eventos ok",
      "corrida completa", "error/no_score", "null", "events.class_name", g4_by_class),
    M("G4-psi-confianza-vs-referencia", "G4", False, "PSI de la distribución de confianza vs referencia", "PSI",
      "Cambio de la distribución de scores respecto a la referencia en 10 bins de 0.1.",
      "Σ (p_run − p_ref)·ln(p_run / p_ref), con piso 1e-4 por bin", "eventos ok de la corrida y de la referencia",
      "corrida completa", "error/no_score", "sin referencia o sin eventos → null", "config/referencia.json + events.score",
      g4_psi_vs_reference),
    M("G5-cobertura-trazabilidad", "G5", True, "Cobertura de trazabilidad", "proporción",
      "Eventos con todos los campos requeridos y referencias resolubles (frame↔evento, config, imagen con SHA-256).",
      "n(eventos completos) / n(eventos)", "todos los eventos de la corrida", "corrida completa", "ninguna",
      "n=0 → null", "verificación SQL + lectura de capturas", g5_traceability),
    M("G5-imagenes-vinculadas", "G5", False, "Imágenes vinculadas válidas", "proporción",
      "Capturas reclamadas por eventos que existen y coinciden con su SHA-256.",
      "n(image_ref existente y hash correcto) / n(eventos con image_ref)", "eventos con image_ref",
      "corrida completa", "eventos sin captura (política selectiva)", "n=0 → null", "events.image_ref/image_sha256",
      g5_linked_images),
    M("G5-entradas-invalidas", "G5", False, "Entradas inválidas", "proporción",
      "Frames que la fuente no pudo leer o decodificar.", "n(read_error) / n(frames)", "todos los frames",
      "corrida completa", "ninguna", "n=0 → null", "cv2.imread/VideoCapture.read → frames.outcome", g5_invalid_inputs),
    M("G6-version-esperada-vs-reportada", "G6", True, "Versión esperada frente a reportada", "proporción",
      "Fracción de eventos cuyo model_ref coincide con el configurado (model.expected_model_ref).",
      "n(model_ref == esperado) / n(eventos)", "todos los eventos", "corrida completa", "ninguna",
      "n=0 → null", "events.model_ref (hash real del archivo cargado) vs config", g6_version_match),
    M("G6-tiempo-ultimo-reporte", "G6", False, "Tiempo desde el último reporte por fuente", "s",
      "Frescura de cada fuente al cierre de la corrida.", "runs.ended_at − max(events.timestamp) por source_id",
      "eventos por fuente", "instante de cierre", "ninguna", "sin eventos → null", "events.timestamp", g6_last_report_age),
    M("G7-alertas-activas", "G7", True, "Alertas activas", "alertas",
      "Alertas abiertas (sin resolver) al cierre de la corrida; se reporta también el máximo simultáneo.",
      "n(alerts con resolved_at nulo o posterior al cierre)", "alertas de la corrida", "instante de cierre",
      "ninguna", "0 alertas es un valor válido (las reglas sí se evaluaron: ver window_evaluations)",
      "tabla alerts (RuleEngine)", g7_active_alerts),
    M("G7-tiempo-deteccion", "G7", False, "Tiempo de detección", "s",
      "Desde el inicio conocido de la condición (declarado en el manifiesto del escenario) hasta la primera alerta.",
      "alerts.timestamp − (runs.started_at + condition_onset_s)", "primera alerta de la regla declarada",
      "corrida", "escenarios sin condición declarada", "sin alerta → null, status=sin_alerta",
      "manifiesto del escenario + alerts.timestamp", g7_detection_time),
]
BY_ID = {m.id: m for m in REGISTRY}


def context_for(store: Store, run_id: str, cfg=None, scenario_spec: dict | None = None) -> dict:
    run = store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    src = json.loads(run["source_json"])
    canon = json.loads(store.one("SELECT canonical_json FROM configs WHERE config_ref=?", (run["config_ref"],))
                       ["canonical_json"])
    ref = json.loads(run["reference_json"]) if run["reference_json"] else None
    if ref is None and cfg is not None:
        from .rules import load_reference
        ref, _ = load_reference(cfg)
    ctx = {"declared_fps": src.get("fps"), "expected_model_ref": canon["model"]["expected_model_ref"],
           "reference": ref}
    if scenario_spec and scenario_spec.get("condition"):
        ctx["condition_onset_s"] = scenario_spec["condition"].get("onset_s")
        ctx["condition_rule"] = scenario_spec["condition"].get("rule_id", "baja-confianza-v1")
    return ctx


def compute_all(store: Store, run_id: str, ctx: dict, only_base: bool = False) -> dict:
    out = {}
    for m in REGISTRY:
        if only_base and not m.base:
            continue
        res = m.compute(store, run_id, ctx)
        out[m.id] = {"group": m.group, "base": m.base, "name": m.name, "unit": m.unit, **res}
    return out


def write_inventory(path: Path) -> None:
    path.write_text(json.dumps([m.spec() for m in REGISTRY], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
