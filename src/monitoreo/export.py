"""Exporta una corrida a archivos planos recalculables (CSV/JSONL/JSON)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import metrics
from .store import Store


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def export_run(store: Store, run_id: str, out: Path, ctx: dict) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    run = store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if run is None:
        raise ValueError(f"No existe la corrida {run_id}")
    cfg = store.one("SELECT * FROM configs WHERE config_ref=?", (run["config_ref"],))
    (out / "config.yaml").write_text(cfg["raw_yaml"], encoding="utf-8")
    run_doc = {**{k: v for k, v in run.items() if not k.endswith("_json")},
               "model": json.loads(run["model_json"]), "source": json.loads(run["source_json"]),
               "reference": json.loads(run["reference_json"]) if run["reference_json"] else None,
               "config_canonical": json.loads(cfg["canonical_json"]), "store_db": str(store.db_path.name)}
    (out / "run.json").write_text(json.dumps(run_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _csv(out / "frames.csv", store.query("SELECT * FROM frames WHERE run_id=? ORDER BY seq", (run_id,)))
    _jsonl(out / "events.jsonl", store.query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,)))
    _csv(out / "window_evaluations.csv",
         store.query("SELECT * FROM window_evaluations WHERE run_id=? ORDER BY window_start, rule_id", (run_id,)))
    _csv(out / "resource_samples.csv",
         store.query("SELECT * FROM resource_samples WHERE run_id=? ORDER BY timestamp", (run_id,)))
    _jsonl(out / "source_events.jsonl",
           store.query("SELECT * FROM source_events WHERE run_id=? ORDER BY id", (run_id,)))
    alerts = store.query("SELECT * FROM alerts WHERE run_id=? ORDER BY timestamp", (run_id,))
    for a in alerts:
        a["evidence"] = json.loads(a.pop("evidence_json"))
        a["event_ids"] = json.loads(a.pop("event_ids_json"))
    (out / "alerts.json").write_text(json.dumps(alerts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    res = metrics.compute_all(store, run_id, ctx)
    doc = {"run_id": run_id, "config_ref": run["config_ref"], "model_ref": run["model_ref"],
           "git_commit": run["git_commit"], "context": {k: v for k, v in ctx.items() if k != "reference"},
           "metrics": res}
    (out / "metricas.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc
