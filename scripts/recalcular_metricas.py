"""Recalcula las métricas base (G1–G7) desde la exportación de una corrida, sin usar el paquete.

Solo biblioteca estándar. Lee frames.csv, events.jsonl, resource_samples.csv, alerts.json y
run.json de la carpeta exportada y compara con metricas.json.

Uso:
    python scripts/recalcular_metricas.py evidencias/escenarios/c1_normal --store evidencias/almacen

Sale con código 1 si alguna métrica base difiere en más de 1e-9 (relativo).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    h = (len(xs) - 1) * p
    lo, hi = math.floor(h), math.ceil(h)
    return xs[lo] + (xs[hi] - xs[lo]) * (h - lo)


def num(v):
    return None if v in ("", None) else float(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--store", required=True, help="Carpeta del store (para resolver image_ref)")
    a = ap.parse_args()
    d, store = Path(a.export_dir), Path(a.store)

    run = json.loads((d / "run.json").read_text(encoding="utf-8"))
    frames = list(csv.DictReader(open(d / "frames.csv", encoding="utf-8")))
    events = [json.loads(line) for line in open(d / "events.jsonl", encoding="utf-8")]
    res = list(csv.DictReader(open(d / "resource_samples.csv", encoding="utf-8")))
    alerts = json.loads((d / "alerts.json").read_text(encoding="utf-8"))
    reported = json.loads((d / "metricas.json").read_text(encoding="utf-8"))["metrics"]
    ok = [e for e in events if e["status"] == "ok"]

    calc = {}
    # G1 · p95 del tiempo de inferencia
    calc["G1-tiempo-inferencia-p95"] = pct([e["inference_ms"] for e in ok if e["inference_ms"] is not None], 0.95)
    # G2 · FPS de captura
    rx = sorted((int(f["seq"]), float(f["received_at"])) for f in frames if f["outcome"] != "read_error")
    calc["G2-fps-captura"] = (len(rx) - 1) / (rx[-1][1] - rx[0][1]) if len(rx) > 1 else None
    # G3 · CPU media del proceso
    cpu = [num(r["process_cpu_pct"]) for r in res if num(r["process_cpu_pct"]) is not None]
    calc["G3-cpu-proceso"] = sum(cpu) / len(cpu) if cpu else None
    # G4 · proporción de baja confianza (recalculada desde el score y el umbral, no desde la zona guardada)
    lower = run["config_canonical"]["thresholds"]["lower"]
    scored = [e for e in ok if e["score"] is not None]
    calc["G4-proporcion-baja-confianza"] = (sum(e["score"] < lower for e in scored) / len(scored)) if scored else None
    # G5 · cobertura de trazabilidad
    frame_event = {f["frame_id"]: f["event_id"] for f in frames}
    req_all = ("event_id", "run_id", "frame_id", "timestamp", "source_id", "task", "model_ref", "config_ref",
               "status", "lower_threshold", "upper_threshold")
    req_ok = ("class_id", "class_name", "score", "zone", "top5_json")
    complete = 0
    for e in events:
        good = all(e[k] is not None for k in req_all)
        good &= e["status"] != "ok" or all(e[k] is not None for k in req_ok)
        good &= frame_event.get(e["frame_id"]) == e["event_id"]
        good &= e["config_ref"] == run["config_ref"]
        if e["image_ref"]:
            p = store / e["image_ref"]
            good &= p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == e["image_sha256"]
        complete += bool(good)
    calc["G5-cobertura-trazabilidad"] = complete / len(events) if events else None
    # G6 · versión esperada vs reportada
    exp = run["config_canonical"]["model"]["expected_model_ref"]
    calc["G6-version-esperada-vs-reportada"] = (sum(e["model_ref"] == exp for e in events) / len(events)
                                                if events else None)
    # G7 · alertas activas al cierre
    end = run["ended_at"]
    calc["G7-alertas-activas"] = sum(1 for x in alerts if x["resolved_at"] is None or x["resolved_at"] > end)

    bad = 0
    print(f"Corrida {run['run_id']}  config {run['config_ref']}  commit {run['git_commit']}")
    print(f"{'métrica':38s} {'reportado':>14s} {'recalculado':>14s}  estado")
    for k, v in calc.items():
        r = reported[k]["value"]
        same = (r is None and v is None) or (r is not None and v is not None and
                                             math.isclose(r, v, rel_tol=1e-9, abs_tol=1e-12))
        bad += not same
        fmt = lambda x: "null" if x is None else f"{x:.6g}"  # noqa: E731
        print(f"{k:38s} {fmt(r):>14s} {fmt(v):>14s}  {'OK' if same else 'DIFERENTE'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
