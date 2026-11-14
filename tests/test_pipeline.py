import cv2
import numpy as np
import pytest

from monitoreo.config import SourceCfg, load_config
from monitoreo.pipeline import Pipeline
from monitoreo.policy import zone_for
from monitoreo.store import Store


def test_zonas_limites():
    assert zone_for(0.29, 0.3, 0.6) == "low"
    assert zone_for(0.30, 0.3, 0.6) == "doubt"   # igualdad con el inferior -> duda
    assert zone_for(0.60, 0.3, 0.6) == "accepted"  # igualdad con el superior -> aceptada
    assert zone_for(None, 0.3, 0.6) is None


def test_umbrales_invalidos():
    from monitoreo.config import ThresholdsCfg
    with pytest.raises(ValueError):
        ThresholdsCfg(lower=0.7, upper=0.6)


def test_pipeline_registra_frames_eventos_y_capturas(tmp_path):
    seq = tmp_path / "seq"
    seq.mkdir()
    for i in range(4):
        cv2.imwrite(str(seq / f"{i:03d}.jpg"), np.full((120, 160, 3), 40 * i, np.uint8))
    (seq / "004.jpg").write_bytes(b"no es una imagen")  # entrada inválida

    cfg = load_config()
    cfg.sampling.infer_every_n = 2
    src = SourceCfg(source_id="test", kind="sequence", path=str(seq), fps=50)
    store = Store(tmp_path / "store")
    run_id = Pipeline(cfg, src, store, scenario="test").run()

    outcomes = {r["outcome"]: r["n"] for r in store.query(
        "SELECT outcome, COUNT(*) n FROM frames WHERE run_id=? GROUP BY outcome", (run_id,))}
    assert outcomes == {"inferred": 2, "dropped_sampling": 2, "read_error": 1}
    evs = store.query("SELECT * FROM events WHERE run_id=?", (run_id,))
    assert all(e["model_ref"].startswith("mobilenet_v3_large") and e["config_ref"] == cfg.config_ref for e in evs)
    for e in evs:
        if e["image_ref"]:
            assert store.image_path(e["image_ref"]).exists()
    run = store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    assert run["status"] == "completed"
