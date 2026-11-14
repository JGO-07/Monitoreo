"""Motor de reglas: ventanas temporales consecutivas y alertas.

Ventanas
--------
Ventanas *tumbling* de ``windows.size_s`` segundos alineadas con el inicio de la corrida
(``t0``): la ventana k cubre ``[t0 + k·size, t0 + (k+1)·size)``. Un evento pertenece a la
ventana de su ``timestamp`` (instante de recepción del frame). La ventana se cierra cuando el
reloj supera ``fin + grace_s`` (para incluir eventos aún en proceso) o al terminar la corrida.

Población elegible: eventos con ``status='ok'`` y puntuación. Los eventos fallidos o sin
puntuación se cuentan aparte y no entran en el denominador.

Estados por ventana y regla
---------------------------
* ``insufficient``: ``n_eligible < windows.min_observations``. **No equivale a normalidad**: no
  resuelve alertas activas, no cuenta como incumplimiento e interrumpe la persistencia.
* ``breach``: hay evidencia suficiente y se cumple la condición de la regla.
* ``normal``: hay evidencia suficiente y no se cumple la condición; resuelve la alerta activa.

Reglas
------
* ``low_confidence``: ``low_rate = n_low / n_eligible >= min_low_rate`` durante
  ``consecutive_windows`` ventanas suficientes seguidas.
* ``sustained_change``: ``low_rate - referencia.low_rate >= tolerance`` durante
  ``consecutive_windows`` ventanas suficientes seguidas (cambio sostenido frente a la referencia).
* ``insufficient_data``: ``consecutive_windows`` ventanas completas seguidas en estado
  ``insufficient`` (la fuente no aporta observaciones suficientes).

Mientras una alerta está activa, las ventanas siguientes en ``breach`` la actualizan
(``last_seen``, eventos) en lugar de crear alertas repetidas.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time

from . import prom
from .config import MonitorConfig, resolve
from .store import Store, dumps

RULE_KINDS = ("low_confidence", "sustained_change", "insufficient_data")


def load_reference(cfg: MonitorConfig) -> tuple[dict | None, str | None]:
    """Devuelve (referencia, reference_ref) o (None, None) si no está configurada o no existe."""
    if not cfg.reference or not cfg.reference.get("file"):
        return None, None
    p = resolve(cfg.reference["file"])
    if not p.exists():
        return None, None
    data = p.read_bytes()
    return json.loads(data), f"ref-{hashlib.sha256(data).hexdigest()[:8]}"


class RuleEngine:
    def __init__(self, cfg: MonitorConfig, store: Store, run_id: str):
        self.cfg, self.store, self.run_id = cfg, store, run_id
        w = cfg.windows or {}
        self.size = float(w.get("size_s", 10.0))
        self.min_obs = int(w.get("min_observations", 10))
        self.grace = float(w.get("grace_s", 1.5))
        self.rules = {k: v for k, v in (cfg.alerts or {}).items() if k in RULE_KINDS}
        self.reference, self.reference_ref = load_reference(cfg)
        if "sustained_change" in self.rules and self.reference is None:
            # sin referencia no se puede evaluar el cambio sostenido: se registra, no se inventa
            self.rules.pop("sustained_change")
            self._missing_reference = True
        else:
            self._missing_reference = False
        self.t0: float | None = None
        self.next_k = 0
        self.buffer: dict[int, list[dict]] = {}
        self.streak = {k: 0 for k in self.rules}
        self.active: dict[str, dict] = {}       # rule_kind -> alerta activa (fila)
        self.alert_seq = 0
        self._history: dict[str, list[dict]] = {}
        self.lock = threading.Lock()

    # ---- integración con el pipeline -------------------------------------------
    def begin(self, t0: float) -> None:
        self.t0 = t0
        if self._missing_reference:
            self.store.insert("source_events", {"run_id": self.run_id, "timestamp": time.time(),
                                                "kind": "rule_disabled",
                                                "detail_json": dumps({"rule": "sustained_change",
                                                                      "reason": "referencia no disponible"})})

    def on_event(self, ev: dict) -> None:
        with self.lock:
            k = int(math.floor((ev["timestamp"] - self.t0) / self.size))
            self.buffer.setdefault(k, []).append(ev)

    def tick(self, now: float) -> None:
        with self.lock:
            while now >= self._end(self.next_k) + self.grace:
                self._close(self.next_k, partial=False)
                self.next_k += 1

    def finalize(self, now: float) -> None:
        """Cierra las ventanas restantes; la última puede ser parcial."""
        with self.lock:
            last_k = max(self.buffer) if self.buffer else self.next_k - 1
            last_k = max(last_k, int(math.floor((now - self.t0) / self.size)))
            while self.next_k <= last_k:
                partial = now < self._end(self.next_k)
                self._close(self.next_k, partial=partial)
                self.next_k += 1

    # ---- cálculo de ventanas -----------------------------------------------------
    def _start(self, k: int) -> float:
        return self.t0 + k * self.size

    def _end(self, k: int) -> float:
        return self.t0 + (k + 1) * self.size

    def _close(self, k: int, partial: bool) -> None:
        evs = self.buffer.pop(k, [])
        elig = [e for e in evs if e["status"] == "ok" and e["score"] is not None]
        n, n_low = len(elig), sum(1 for e in elig if e["zone"] == "low")
        low_rate = n_low / n if n else None
        mean_score = sum(e["score"] for e in elig) / n if n else None
        sufficient = n >= self.min_obs
        win = {
            "k": k, "start": self._start(k), "end": self._end(k), "n": n, "n_low": n_low,
            "n_doubt": sum(1 for e in elig if e["zone"] == "doubt"),
            "n_accepted": sum(1 for e in elig if e["zone"] == "accepted"),
            "n_not_eligible": len(evs) - n, "low_rate": low_rate, "mean_score": mean_score,
            "partial": partial, "event_ids": [e["event_id"] for e in evs],
        }
        for kind, rule in self.rules.items():
            state, extra = self._evaluate(kind, rule, win, sufficient)
            self._apply(kind, rule, state, win, extra)

    def _evaluate(self, kind: str, rule: dict, win: dict, sufficient: bool) -> tuple[str, dict]:
        if kind == "insufficient_data":
            if win["partial"]:
                return "skipped_partial", {}
            return ("breach" if not sufficient else "normal"), {}
        if not sufficient:
            return "insufficient", {}
        if kind == "low_confidence":
            return ("breach" if win["low_rate"] >= rule["min_low_rate"] else "normal"), {
                "min_low_rate": rule["min_low_rate"]}
        # sustained_change
        ref_rate = self.reference["low_rate"]
        delta = win["low_rate"] - ref_rate
        return ("breach" if delta >= rule["tolerance"] else "normal"), {
            "reference_low_rate": ref_rate, "delta": delta, "tolerance": rule["tolerance"],
            "reference_ref": self.reference_ref}

    def _apply(self, kind: str, rule: dict, state: str, win: dict, extra: dict) -> None:
        need = int(rule.get("consecutive_windows", 1))
        if state == "breach":
            self.streak[kind] += 1
        elif state != "skipped_partial":
            self.streak[kind] = 0  # normal o insufficient interrumpen la persistencia
        detail = {**extra, "partial": win["partial"], "n_doubt": win["n_doubt"],
                  "n_accepted": win["n_accepted"], "n_not_eligible": win["n_not_eligible"]}
        self.store.insert("window_evaluations", {
            "run_id": self.run_id, "rule_id": rule["rule_id"], "window_start": win["start"],
            "window_end": win["end"], "n_eligible": win["n"], "n_low": win["n_low"],
            "low_rate": win["low_rate"], "mean_score": win["mean_score"], "state": state,
            "streak": self.streak[kind], "detail_json": dumps(detail),
        })
        hist = self._history.setdefault(kind, [])
        hist.append({**win, "state": state, **extra})
        del hist[:-max(need, 1)]

        active = self.active.get(kind)
        now = time.time()
        if state == "breach" and active is not None:
            ids = json.loads(active["event_ids_json"]) + win["event_ids"]
            ev = json.loads(active["evidence_json"])
            ev["windows"].append(self._win_summary(win, extra))
            self.store.execute(
                "UPDATE alerts SET last_seen=?, window_end=?, event_ids_json=?, evidence_json=?, query=? "
                "WHERE alert_id=?",
                (now, win["end"], dumps(ids), dumps(ev), self._query(active["window_start"], win["end"]),
                 active["alert_id"]))
            active.update(event_ids_json=dumps(ids), evidence_json=dumps(ev), window_end=win["end"])
        elif state == "breach" and self.streak[kind] >= need:
            self._fire(kind, rule, hist[-need:])
        elif state == "normal" and active is not None:
            self.store.execute("UPDATE alerts SET status='resolved', resolved_at=? WHERE alert_id=?",
                               (now, active["alert_id"]))
            self.active.pop(kind)
        prom.ALERTS_ACTIVE.labels(rule["rule_id"]).set(1 if kind in self.active else 0)

    def _win_summary(self, w: dict, extra: dict) -> dict:
        return {"start": w["start"], "end": w["end"], "n": w["n"], "n_low": w["n_low"],
                "low_rate": w["low_rate"], "mean_score": w["mean_score"], "partial": w["partial"],
                **{k: v for k, v in extra.items() if k in ("delta", "reference_low_rate")}}

    def _query(self, start: float, end: float) -> str:
        return (f"SELECT * FROM events WHERE run_id='{self.run_id}' "
                f"AND timestamp >= {start!r} AND timestamp < {end!r} ORDER BY seq")

    def _fire(self, kind: str, rule: dict, wins: list[dict]) -> None:
        self.alert_seq += 1
        start, end = wins[0]["start"], wins[-1]["end"]
        ids = [i for w in wins for i in w["event_ids"]]
        last = wins[-1]
        th = self.cfg.thresholds
        if kind == "low_confidence":
            sev = "critical" if last["low_rate"] >= rule.get("critical_low_rate", 2) else rule.get("severity", "warning")
            reason = (f"Proporción de baja confianza {last['n_low']}/{last['n']} = {last['low_rate']:.2f} "
                      f">= {rule['min_low_rate']:.2f} (score < {th.lower:.2f}) en {len(wins)} ventana(s) de "
                      f"{self.size:.0f} s con n >= {self.min_obs}.")
        elif kind == "sustained_change":
            sev = rule.get("severity", "warning")
            rates = ", ".join(f"{w['low_rate']:.2f}" for w in wins)
            reason = (f"Cambio sostenido: proporción de baja confianza [{rates}] supera la referencia "
                      f"{self.reference['low_rate']:.2f} en >= {rule['tolerance']:.2f} durante {len(wins)} "
                      f"ventanas consecutivas.")
        else:
            sev = rule.get("severity", "info")
            reason = (f"Evidencia insuficiente: {len(wins)} ventanas consecutivas con menos de {self.min_obs} "
                      f"inferencias elegibles (n = {', '.join(str(w['n']) for w in wins)}). No implica normalidad.")
        alert = {
            "alert_id": f"alert-{self.run_id[4:]}-{self.alert_seq:03d}", "run_id": self.run_id,
            "rule_id": rule["rule_id"], "timestamp": time.time(), "last_seen": time.time(),
            "resolved_at": None, "status": "active", "severity": sev, "reason": reason,
            "suggested_action": rule["suggested_action"], "config_ref": self.cfg.config_ref,
            "window_start": start, "window_end": end,
            "evidence_json": dumps({"rule_kind": kind, "rule": rule, "lower_threshold": th.lower,
                                    "upper_threshold": th.upper, "min_observations": self.min_obs,
                                    "window_size_s": self.size, "reference_ref": self.reference_ref,
                                    "windows": [self._win_summary(w, w) for w in wins]}),
            "event_ids_json": dumps(ids), "query": self._query(start, end),
        }
        self.store.insert("alerts", alert)
        self.active[kind] = alert
