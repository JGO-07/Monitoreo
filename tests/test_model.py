import cv2
import numpy as np
import pytest

from monitoreo.model import EXPECTED_SHA256, ROOT, Classifier


@pytest.fixture(scope="module")
def clf():
    return Classifier()


def test_identidad_del_artefacto(clf):
    assert clf.sha256 == EXPECTED_SHA256
    ident = clf.identity()
    assert ident["num_classes"] == 1000
    assert ident["preprocessing"]["center_crop"] == 224


def test_inferencia_real_reproducible(clf):
    img = cv2.imread(str(ROOT / "data" / "muestras" / "coffee.jpg"))
    a, b = clf.predict(img), clf.predict(img)
    assert a.class_name == "espresso"
    assert a.score == b.score  # determinista en CPU
    assert 0.0 <= a.score <= 1.0
    assert abs(sum(t["score"] for t in a.top5) - 1.0) < 1.0  # top5 es subconjunto del softmax


def test_imagen_uniforme_da_score_bajo(clf):
    gris = np.full((240, 320, 3), 128, dtype=np.uint8)
    assert clf.predict(gris).score < 0.5
