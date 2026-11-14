import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from monitoreo.config import SourceCfg, load_config
from monitoreo.pipeline import Pipeline
from monitoreo.portal import create_app
from monitoreo.rules import RuleEngine
from monitoreo.store import Store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("portal")
    seq = tmp / "seq"
    seq.mkdir()
    for i in range(6):  # imágenes uniformes -> scores bajos reales del modelo
        cv2.imwrite(str(seq / f"{i:03d}.jpg"), np.full((120, 160, 3), 30 * i, np.uint8))
    cfg = load_config()
    store = Store(tmp / "store")
    pipe = Pipeline(cfg, SourceCfg(source_id="t", kind="sequence", path=str(seq), fps=50), store, scenario="t",
                    rule_engine_factory=lambda p: RuleEngine(p.cfg, p.store, p.run_id))
    run_id = pipe.run()
    return TestClient(create_app(tmp / "store")), store, run_id


def test_vistas_obligatorias(client):
    c, store, run_id = client
    assert c.get("/eventos").status_code == 200
    assert c.get("/alertas").status_code == 200
    ev = store.one("SELECT * FROM events WHERE run_id=? AND image_ref IS NOT NULL", (run_id,))
    page = c.get(f"/eventos/{ev['event_id']}").text
    assert ev["config_ref"] in page and ev["image_ref"][9:] in page and ev["model_ref"] in page
    img = c.get("/capturas/" + ev["image_ref"][9:])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_filtros_y_estados_vacios(client):
    c, _, run_id = client
    assert "Ningún evento cumple" in c.get("/eventos?class_name=zzzz").text
    assert c.get("/eventos?since=no-es-fecha").status_code == 400
    assert c.get("/eventos/inexistente").status_code == 404
    assert c.get("/alertas/inexistente").status_code == 404
    data = c.get(f"/api/events?run_id={run_id}&limit=2").json()
    assert data["total"] >= 2 and len(data["items"]) == 2
    csv_text = c.get(f"/api/events.csv?run_id={run_id}").text
    assert csv_text.splitlines()[0].startswith("event_id,")


def test_corrida_y_metricas(client):
    c, _, run_id = client
    assert c.get(f"/corridas/{run_id}").status_code == 200
    m = c.get(f"/api/runs/{run_id}/metrics").json()
    assert {v["group"] for v in m.values() if v["base"]} == {f"G{i}" for i in range(1, 8)}
    assert "cv_inferences_total" in c.get("/metrics").text
