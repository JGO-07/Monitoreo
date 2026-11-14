"""Referencia de confianza construida desde una corrida real en condiciones representativas.

``propose``: analiza los scores top-1 de una corrida de referencia (cuantiles, por clase y por
ventana) y evalúa pares de umbrales candidatos. Los scores no dependen de los umbrales, así que
el análisis puede hacerse con umbrales provisionales; la decisión se documenta en
``docs/umbrales.md`` y la referencia se vuelve a grabar con la configuración final.

``build``: escribe ``config/referencia.json`` desde una corrida ejecutada con la configuración
final (misma ``config_ref``); falla si la corrida usó otra configuración o modelo.
"""

from __future__ import annotations

import json
from collections import Counter

import numpy as np

from .config import MonitorConfig, resolve
from .store import Store

QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


def _scores(store: Store, run_id: str) -> tuple[list[dict], np.ndarray]:
    evs = store.query("SELECT * FROM events WHERE run_id=? AND status='ok' AND score IS NOT NULL ORDER BY seq",
                      (run_id,))
    if not evs:
        raise ValueError(f"La corrida {run_id} no tiene inferencias elegibles")
    return evs, np.array([e["score"] for e in evs], dtype=float)


CANDIDATES = ((0.10, 0.40), (0.15, 0.40), (0.15, 0.45), (0.20, 0.40), (0.20, 0.45), (0.25, 0.45), (0.25, 0.50))


def propose(store: Store, run_id: str, window_s: float = 10.0, min_obs: int = 10) -> dict:
    """Análisis de la referencia para elegir umbrales (no decide: documenta la evidencia).

    Para cada par candidato calcula la proporción de zonas en la referencia y la proporción de
    baja confianza por ventana (mismas ventanas que el motor de reglas), para comprobar que la
    regla de alerta no se dispara en condiciones normales.
    """
    evs, s = _scores(store, run_id)
    run = store.one("SELECT started_at FROM runs WHERE run_id=?", (run_id,))
    k = np.floor((np.array([e["timestamp"] for e in evs]) - run["started_at"]) / window_s).astype(int)
    by_class: dict[str, list[float]] = {}
    for e in evs:
        by_class.setdefault(e["class_name"], []).append(e["score"])
    cands = []
    for lo, up in CANDIDATES:
        wr = [float((s[k == i] < lo).mean()) for i in range(int(k.max()) + 1) if (k == i).sum() >= min_obs]
        cands.append({"lower": lo, "upper": up, "low_rate": float((s < lo).mean()),
                      "doubt_rate": float(((s >= lo) & (s < up)).mean()), "accepted_rate": float((s >= up).mean()),
                      "window_low_rates": [round(x, 4) for x in wr], "max_window_low_rate": max(wr) if wr else None})
    return {
        "run_id": run_id, "n": len(s), "mean": float(s.mean()),
        "quantiles": {f"p{int(p * 100):02d}": float(np.quantile(s, p)) for p in QUANTILES},
        "window_s": window_s, "events_per_window": np.bincount(k).tolist(),
        "by_class": sorted(({"class_name": c, "n": len(v), "mean": float(np.mean(v)), "min": float(min(v)),
                             "max": float(max(v))} for c, v in by_class.items()), key=lambda d: -d["n"]),
        "candidates": cands,
        "scores_by_seq": [{"seq": e["seq"], "event_id": e["event_id"], "class_name": e["class_name"],
                           "score": e["score"]} for e in evs],
    }


def build(store: Store, run_id: str, cfg: MonitorConfig) -> dict:
    run = store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if run is None:
        raise ValueError(f"No existe la corrida {run_id}")
    if run["config_ref"] != cfg.config_ref:
        raise ValueError(f"La corrida usó {run['config_ref']} y la configuración actual es {cfg.config_ref}; "
                         "vuelve a ejecutar la referencia con la configuración final.")
    if run["model_ref"] != cfg.model.expected_model_ref:
        raise ValueError("La corrida no usó el modelo esperado")
    evs, s = _scores(store, run_id)
    lower, upper = cfg.thresholds.lower, cfg.thresholds.upper
    hist, edges = np.histogram(s, bins=10, range=(0.0, 1.0))
    wins = store.query("SELECT window_start, window_end, n_eligible, n_low, low_rate, state FROM window_evaluations "
                       "WHERE run_id=? AND rule_id=? ORDER BY window_start",
                       (run_id, (cfg.alerts or {}).get("low_confidence", {}).get("rule_id", "")))
    suff = [w["low_rate"] for w in wins if w["state"] != "insufficient" and w["low_rate"] is not None]
    n_all = store.one("SELECT COUNT(*) n FROM events WHERE run_id=?", (run_id,))["n"]
    return {
        "reference_id": f"referencia-{run_id}",
        "run_id": run_id,
        "scenario": run["scenario"],
        "source_id": run["source_id"],
        "config_ref": run["config_ref"],
        "model_ref": run["model_ref"],
        "population": "eventos status=ok con score top-1 (clasificación, unidad = frame inferido)",
        "n_events": n_all,
        "n_eligible": len(s),
        "thresholds": {"lower": lower, "upper": upper},
        "low_rate": float((s < lower).mean()),
        "doubt_rate": float(((s >= lower) & (s < upper)).mean()),
        "accepted_rate": float((s >= upper).mean()),
        "mean_score": float(s.mean()),
        "quantiles": {f"p{int(p * 100):02d}": float(np.quantile(s, p)) for p in QUANTILES},
        "histogram": {"edges": [round(float(e), 2) for e in edges], "counts": [int(c) for c in hist]},
        "window_low_rates": {"n_windows_sufficient": len(suff),
                             "mean": float(np.mean(suff)) if suff else None,
                             "max": float(np.max(suff)) if suff else None},
        "top_classes": [{"class_name": c, "n": n} for c, n in
                        Counter(e["class_name"] for e in evs).most_common(10)],
    }


def write(ref: dict, cfg: MonitorConfig) -> str:
    path = resolve(cfg.reference["file"])
    path.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)
