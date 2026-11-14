"""Pruebas de la lógica de reglas con eventos sintéticos.

Complementan (no sustituyen) los escenarios C1/C2, que usan inferencias reales.
"""

import json

import pytest

from monitoreo.config import load_config
from monitoreo.rules import RuleEngine
from monitoreo.store import Store

T0 = 1_000_000.0


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    cfg = load_config()
    store = Store(tmp_path / "s")
    eng = RuleEngine(cfg, store, "run-test")
    eng.reference, eng.reference_ref = {"low_rate": 0.05}, "ref-test"
    eng.rules = {k: v for k, v in (cfg.alerts or {}).items()}
    eng.streak = {k: 0 for k in eng.rules}
    eng.begin(T0)
    return eng, store, cfg


def feed(eng, k, n, n_low, lower):
    for i in range(n):
        score = lower - 0.1 if i < n_low else 0.95
        eng.on_event({"event_id": f"e{k}-{i}", "timestamp": T0 + k * eng.size + i * 0.1, "status": "ok",
                      "score": score, "zone": "low" if i < n_low else "accepted"})


def test_ventana_insuficiente_no_es_normal_ni_resuelve(engine):
    eng, store, cfg = engine
    lo = cfg.thresholds.lower
    feed(eng, 0, 20, 15, lo)          # breach baja confianza
    feed(eng, 1, 3, 0, lo)            # insuficiente
    feed(eng, 2, 20, 0, lo)           # normal -> resuelve
    eng.finalize(T0 + 3 * eng.size + 5)
    rows = store.query("SELECT state FROM window_evaluations WHERE rule_id='baja-confianza-v1' ORDER BY window_start")
    assert [r["state"] for r in rows][:3] == ["breach", "insufficient", "normal"]
    a = store.query("SELECT * FROM alerts WHERE rule_id='baja-confianza-v1'")
    assert len(a) == 1 and a[0]["status"] == "resolved"
    ev = json.loads(a[0]["evidence_json"])
    assert ev["windows"][0]["n_low"] == 15 and ev["windows"][0]["n"] == 20
    assert len(json.loads(a[0]["event_ids_json"])) == 20


def test_no_duplica_alertas_mientras_sigue_activa(engine):
    eng, store, cfg = engine
    for k in range(4):
        feed(eng, k, 20, 18, cfg.thresholds.lower)
    eng.finalize(T0 + 4 * eng.size + 5)
    lc = store.query("SELECT * FROM alerts WHERE rule_id='baja-confianza-v1'")
    assert len(lc) == 1 and lc[0]["severity"] == "critical"
    assert len(json.loads(lc[0]["evidence_json"])["windows"]) == 4
    # cambio sostenido: requiere 3 ventanas consecutivas
    sc = store.query("SELECT * FROM alerts WHERE rule_id='cambio-sostenido-v1'")
    assert len(sc) == 1


def test_cambio_sostenido_se_interrumpe_con_ventana_insuficiente(engine):
    eng, store, cfg = engine
    lo = cfg.thresholds.lower
    feed(eng, 0, 20, 8, lo)   # 0.40 >= 0.05 + 0.25 pero < 0.5
    feed(eng, 1, 20, 8, lo)
    feed(eng, 2, 2, 2, lo)    # insuficiente: interrumpe la persistencia
    feed(eng, 3, 20, 8, lo)
    eng.finalize(T0 + 4 * eng.size + 5)
    assert store.query("SELECT * FROM alerts WHERE rule_id='cambio-sostenido-v1'") == []
    assert store.query("SELECT * FROM alerts WHERE rule_id='baja-confianza-v1'") == []


def test_evidencia_insuficiente_dispara_tras_dos_ventanas(engine):
    eng, store, cfg = engine
    feed(eng, 0, 20, 0, cfg.thresholds.lower)
    eng.finalize(T0 + 3 * eng.size + 0.0)   # ventanas 1 y 2 vacías y completas
    a = store.query("SELECT * FROM alerts WHERE rule_id='evidencia-insuficiente-v1'")
    assert len(a) == 1 and a[0]["severity"] == "info"
