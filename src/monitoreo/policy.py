"""Política de confianza: dos umbrales y tres zonas, más estados aparte.

- ``s < lower``            -> ``low``      (baja confianza)
- ``lower <= s < upper``   -> ``doubt``    (zona de duda)
- ``s >= upper``           -> ``accepted`` (aceptada por la política; NO implica acierto)

Una inferencia fallida (``status=error``) o sin puntuación (``status=no_score``) no recibe zona
ni entra en los denominadores de las tasas de confianza.
"""

from __future__ import annotations

import math

ZONES = ("accepted", "doubt", "low")


def zone_for(score: float | None, lower: float, upper: float) -> str | None:
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return None
    if score < lower:
        return "low"
    if score < upper:
        return "doubt"
    return "accepted"
