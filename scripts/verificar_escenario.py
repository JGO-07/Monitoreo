"""Verifica las expectativas declaradas en el manifiesto de un escenario contra una corrida.

Uso:
    python scripts/verificar_escenario.py --store evidencias/almacen --run-id <run_id> \
        --scenario-file escenarios/s01_interrupcion.yaml

Expectativas soportadas (bloque ``expected`` del manifiesto):
  alerts_present / alerts_absent      reglas que deben / no deben generar alertas
  alerts_resolved                     reglas cuyas alertas deben estar resueltas al cierre
  source_events_present               tipos de source_events que deben existir
  windows_insufficient_during_condition
                                      reglas cuyas ventanas completamente dentro de
                                      [onset_s, recovery_s) deben estar en 'insufficient'
  sufficient_window_after_recovery    reglas con al menos una ventana suficiente que empiece
                                      después de recovery_s

Sale con código 1 si alguna expectativa falla. Solo usa la biblioteca estándar.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # disponible en el entorno del proyecto
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:  # pragma: no cover
        raise SystemExit("Se requiere PyYAML para leer el manifiesto")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--scenario-file", required=True)
    a = ap.parse_args()
    spec = load_yaml(Path(a.scenario_file))
    exp = spec.get("expected", {})
    cond = spec.get("condition", {})
    db = sqlite3.connect(Path(a.store) / "monitoreo.db")
    db.row_factory = sqlite3.Row
    run = db.execute("SELECT * FROM runs WHERE run_id=?", (a.run_id,)).fetchone()
    t0 = run["started_at"]
    alerts = [dict(r) for r in db.execute("SELECT * FROM alerts WHERE run_id=?", (a.run_id,))]
    kinds = {r["kind"] for r in db.execute("SELECT kind FROM source_events WHERE run_id=?", (a.run_id,))}
    results: list[tuple[str, bool, str]] = []

    for rule in exp.get("alerts_present", []):
        hits = [x for x in alerts if x["rule_id"] == rule]
        results.append((f"alerta presente: {rule}", bool(hits),
                        ", ".join(f"{x['alert_id']} ({x['severity']}, t={x['timestamp'] - t0:.1f}s)" for x in hits)
                        or "ninguna"))
    for rule in exp.get("alerts_absent", []):
        hits = [x for x in alerts if x["rule_id"] == rule]
        results.append((f"alerta ausente: {rule}", not hits, f"{len(hits)} alerta(s)"))
    for rule in exp.get("alerts_resolved", []):
        hits = [x for x in alerts if x["rule_id"] == rule]
        ok = bool(hits) and all(x["status"] == "resolved" for x in hits)
        results.append((f"alerta resuelta: {rule}", ok,
                        ", ".join(f"resuelta t={x['resolved_at'] - t0:.1f}s" if x["resolved_at"] else "activa"
                                  for x in hits) or "ninguna"))
    for kind in exp.get("source_events_present", []):
        results.append((f"evento de fuente: {kind}", kind in kinds, ", ".join(sorted(kinds))))

    onset, rec = cond.get("onset_s"), cond.get("recovery_s")
    for rule in exp.get("windows_insufficient_during_condition", []):
        ws = [dict(r) for r in db.execute(
            "SELECT * FROM window_evaluations WHERE run_id=? AND rule_id=? ORDER BY window_start", (a.run_id, rule))]
        inside = [w for w in ws if w["window_start"] - t0 >= onset and w["window_end"] - t0 <= rec]
        ok = bool(inside) and all(w["state"] == "insufficient" for w in inside)
        results.append((f"ventanas insuficientes durante la condición: {rule}", ok,
                        "; ".join(f"[{w['window_start'] - t0:.0f},{w['window_end'] - t0:.0f})s n={w['n_eligible']} "
                                  f"{w['state']}" for w in inside) or "sin ventanas dentro"))
    for rule in exp.get("sufficient_window_after_recovery", []):
        ws = [dict(r) for r in db.execute(
            "SELECT * FROM window_evaluations WHERE run_id=? AND rule_id=? AND state!='insufficient' "
            "ORDER BY window_start", (a.run_id, rule))]
        after = [w for w in ws if w["window_start"] - t0 >= rec]
        results.append((f"ventana suficiente tras la recuperación: {rule}", bool(after),
                        f"primera: [{after[0]['window_start'] - t0:.0f},{after[0]['window_end'] - t0:.0f})s "
                        f"n={after[0]['n_eligible']} {after[0]['state']}" if after else "ninguna"))
    print(f"Escenario {spec['scenario']} · corrida {a.run_id} · commit {run['git_commit']} "
          f"(git_dirty={run['git_dirty']}) · config {run['config_ref']}")
    bad = 0
    for name, ok, detail in results:
        bad += not ok
        print(f"[{'OK' if ok else 'FALLA'}] {name} — {detail}")
    print(f"{len(results) - bad}/{len(results)} expectativas cumplidas")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
