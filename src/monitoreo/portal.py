"""Portal de monitoreo (FastAPI + Jinja).

Vistas obligatorias: consulta de eventos y de alertas, con vínculos a los registros que las
explican (captura, configuración, frame, corrida, ventanas). Vistas adicionales: resumen,
corridas con métricas y gráfica de ventanas, inventario de métricas, exportación CSV de
eventos filtrados y stream en vivo cuando el portal se inicia con un pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics as metrics_mod
from .store import Store

TEMPLATES = Path(__file__).parent / "templates"
PAGE_SIZE = 50
ZONES = ["low", "doubt", "accepted"]


def fmt_ts(t: float | None) -> str:
    if t is None:
        return "—"
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def fmt_num(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def create_app(store_root: str | Path, live=None) -> FastAPI:
    store = Store(store_root)
    app = FastAPI(title="Centro de monitoreo CV", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store, app.state.live = store, live
    app.mount("/capturas", StaticFiles(directory=store.root / "capturas"), name="capturas")
    tpl = Jinja2Templates(directory=str(TEMPLATES))
    tpl.env.filters.update(ts=fmt_ts, num=fmt_num, fromjson=json.loads,
                           pretty=lambda o: json.dumps(o, ensure_ascii=False, sort_keys=True))
    tpl.env.globals.update(live_enabled=live is not None)

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        return tpl.TemplateResponse(request, name, {"store_path": str(store.root), **ctx})

    # ------------------------------------------------------------------ consultas comunes
    def runs() -> list[dict]:
        return store.query("SELECT run_id, scenario, started_at, ended_at, status, config_ref, model_ref, "
                           "source_id, git_commit FROM runs ORDER BY started_at DESC")

    def event_filters(run_id, zone, status, class_name, source_id, has_image, since, until):
        where, params = ["1=1"], []
        if run_id:
            where.append("run_id = ?"); params.append(run_id)
        if zone:
            where.append("zone = ?"); params.append(zone)
        if status:
            where.append("status = ?"); params.append(status)
        if class_name:
            where.append("class_name LIKE ?"); params.append(f"%{class_name}%")
        if source_id:
            where.append("source_id = ?"); params.append(source_id)
        if has_image == "1":
            where.append("image_ref IS NOT NULL")
        elif has_image == "0":
            where.append("image_ref IS NULL")
        for value, op in ((since, ">="), (until, "<=")):
            if value:
                try:
                    t = datetime.fromisoformat(value).timestamp()
                except ValueError:
                    raise HTTPException(400, f"Fecha inválida: {value!r} (usa ISO 8601, p. ej. 2026-11-13T21:00)")
                where.append(f"timestamp {op} ?"); params.append(t)
        return " AND ".join(where), params

    def alert_filters(run_id, rule_id, severity, status):
        where, params = ["1=1"], []
        for col, val in (("run_id", run_id), ("rule_id", rule_id), ("severity", severity), ("status", status)):
            if val:
                where.append(f"{col} = ?"); params.append(val)
        return " AND ".join(where), params

    # ------------------------------------------------------------------ páginas
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        active = store.query("SELECT * FROM alerts WHERE status='active' ORDER BY timestamp DESC")
        recent = store.query("SELECT * FROM alerts ORDER BY timestamp DESC LIMIT 10")
        summary = store.query(
            "SELECT r.run_id, r.scenario, r.started_at, r.status, COUNT(e.event_id) n, "
            "SUM(e.zone='low') n_low, SUM(e.zone='doubt') n_doubt, SUM(e.zone='accepted') n_acc, "
            "SUM(e.status!='ok') n_err, (SELECT COUNT(*) FROM alerts a WHERE a.run_id=r.run_id) n_alerts "
            "FROM runs r LEFT JOIN events e ON e.run_id=r.run_id GROUP BY r.run_id ORDER BY r.started_at DESC")
        return render(request, "index.html", active=active, recent=recent, summary=summary)

    @app.get("/eventos", response_class=HTMLResponse)
    def events_page(request: Request, run_id: str = "", zone: str = "", status: str = "", class_name: str = "",
                    source_id: str = "", has_image: str = "", since: str = "", until: str = "",
                    page: int = Query(1, ge=1)):
        where, params = event_filters(run_id, zone, status, class_name, source_id, has_image, since, until)
        total = store.one(f"SELECT COUNT(*) n FROM events WHERE {where}", params)["n"]
        rows = store.query(f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC, seq DESC LIMIT ? OFFSET ?",
                           params + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
        f = dict(run_id=run_id, zone=zone, status=status, class_name=class_name, source_id=source_id,
                 has_image=has_image, since=since, until=until)
        sources = [r["source_id"] for r in store.query("SELECT DISTINCT source_id FROM events ORDER BY 1")]
        return render(request, "events.html", rows=rows, total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)),
                      f=f, runs=runs(), zones=ZONES, sources=sources,
                      qs="&".join(f"{k}={v}" for k, v in f.items() if v))

    @app.get("/eventos/{event_id}", response_class=HTMLResponse)
    def event_page(request: Request, event_id: str):
        ev = store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
        if ev is None:
            raise HTTPException(404, f"No existe el evento {event_id}")
        frame = store.one("SELECT * FROM frames WHERE frame_id=?", (ev["frame_id"],))
        run = store.one("SELECT * FROM runs WHERE run_id=?", (ev["run_id"],))
        alerts = [a for a in store.query("SELECT * FROM alerts WHERE run_id=?", (ev["run_id"],))
                  if event_id in json.loads(a["event_ids_json"])]
        windows = store.query("SELECT * FROM window_evaluations WHERE run_id=? AND window_start<=? AND window_end>? "
                              "ORDER BY rule_id", (ev["run_id"], ev["timestamp"], ev["timestamp"]))
        image_ok = bool(ev["image_ref"]) and store.image_path(ev["image_ref"]).exists()
        return render(request, "event.html", ev=ev, frame=frame, run=run, alerts=alerts, windows=windows,
                      top5=json.loads(ev["top5_json"]) if ev["top5_json"] else [], image_ok=image_ok)

    @app.get("/alertas", response_class=HTMLResponse)
    def alerts_page(request: Request, run_id: str = "", rule_id: str = "", severity: str = "", status: str = ""):
        where, params = alert_filters(run_id, rule_id, severity, status)
        rows = store.query(f"SELECT * FROM alerts WHERE {where} ORDER BY timestamp DESC", params)
        rules = [r["rule_id"] for r in store.query("SELECT DISTINCT rule_id FROM alerts ORDER BY 1")]
        return render(request, "alerts.html", rows=rows, runs=runs(), rules=rules,
                      f=dict(run_id=run_id, rule_id=rule_id, severity=severity, status=status))

    @app.get("/alertas/{alert_id}", response_class=HTMLResponse)
    def alert_page(request: Request, alert_id: str):
        a = store.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
        if a is None:
            raise HTTPException(404, f"No existe la alerta {alert_id}")
        ids = json.loads(a["event_ids_json"])
        evs = store.query(f"SELECT * FROM events WHERE event_id IN ({','.join('?' * len(ids))}) ORDER BY seq", ids) \
            if ids else []
        windows = store.query("SELECT * FROM window_evaluations WHERE run_id=? AND rule_id=? AND window_start>=? "
                              "AND window_end<=? ORDER BY window_start",
                              (a["run_id"], a["rule_id"], a["window_start"], a["window_end"]))
        return render(request, "alert.html", a=a, evidence=json.loads(a["evidence_json"]), evs=evs, windows=windows,
                      n_captured=sum(1 for e in evs if e["image_ref"]))

    @app.get("/corridas", response_class=HTMLResponse)
    def runs_page(request: Request):
        return render(request, "runs.html", rows=runs())

    @app.get("/corridas/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        run = store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if run is None:
            raise HTTPException(404, f"No existe la corrida {run_id}")
        ctx = metrics_mod.context_for(store, run_id)
        mets = metrics_mod.compute_all(store, run_id, ctx)
        windows = store.query("SELECT * FROM window_evaluations WHERE run_id=? ORDER BY window_start, rule_id",
                              (run_id,))
        lc = [w for w in windows if w["rule_id"] == "baja-confianza-v1"]
        canon = json.loads(store.one("SELECT canonical_json FROM configs WHERE config_ref=?",
                                     (run["config_ref"],))["canonical_json"])
        src_events = store.query("SELECT * FROM source_events WHERE run_id=? ORDER BY id", (run_id,))
        alerts = store.query("SELECT * FROM alerts WHERE run_id=? ORDER BY timestamp", (run_id,))
        return render(request, "run.html", run=run, mets=mets, windows=windows, chart=_chart(lc, run, canon, ctx),
                      src_events=src_events, alerts=alerts, model=json.loads(run["model_json"]),
                      source=json.loads(run["source_json"]))

    @app.get("/config/{config_ref}", response_class=HTMLResponse)
    def config_page(request: Request, config_ref: str):
        c = store.one("SELECT * FROM configs WHERE config_ref=?", (config_ref,))
        if c is None:
            raise HTTPException(404, f"No existe la configuración {config_ref}")
        used = store.query("SELECT run_id, scenario FROM runs WHERE config_ref=? ORDER BY started_at", (config_ref,))
        return render(request, "config.html", c=c, used=used)

    @app.get("/metricas", response_class=HTMLResponse)
    def metrics_page(request: Request):
        return render(request, "metrics.html", registry=[m.spec() for m in metrics_mod.REGISTRY])

    @app.get("/en-vivo", response_class=HTMLResponse)
    def live_page(request: Request):
        if app.state.live is None:
            raise HTTPException(404, "El portal no se inició con un pipeline en vivo (serve --live ...)")
        return render(request, "live.html", run_id=app.state.live.run_id)

    @app.get("/en-vivo/stream.mjpg")
    def live_stream():
        pipe = app.state.live
        if pipe is None:
            raise HTTPException(404, "Sin pipeline en vivo")

        def gen():
            last = None
            while True:
                cur = pipe.latest
                if cur and cur.get("jpeg") and cur is not last:
                    last = cur
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + cur["jpeg"] + b"\r\n"
                if pipe.finished:
                    return
                time.sleep(0.05)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    # ------------------------------------------------------------------ API JSON y exportación
    @app.get("/api/events")
    def api_events(run_id: str = "", zone: str = "", status: str = "", class_name: str = "", source_id: str = "",
                   has_image: str = "", since: str = "", until: str = "", limit: int = Query(100, ge=1, le=5000),
                   offset: int = Query(0, ge=0)):
        where, params = event_filters(run_id, zone, status, class_name, source_id, has_image, since, until)
        total = store.one(f"SELECT COUNT(*) n FROM events WHERE {where}", params)["n"]
        rows = store.query(f"SELECT * FROM events WHERE {where} ORDER BY timestamp, seq LIMIT ? OFFSET ?",
                           params + [limit, offset])
        return {"total": total, "items": rows}

    @app.get("/api/events.csv")
    def api_events_csv(run_id: str = "", zone: str = "", status: str = "", class_name: str = "",
                       source_id: str = "", has_image: str = "", since: str = "", until: str = ""):
        where, params = event_filters(run_id, zone, status, class_name, source_id, has_image, since, until)
        rows = store.query(f"SELECT * FROM events WHERE {where} ORDER BY timestamp, seq", params)
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="eventos.csv"',
                                 "X-Total-Count": str(len(rows))})

    @app.get("/api/events/{event_id}")
    def api_event(event_id: str):
        ev = store.one("SELECT * FROM events WHERE event_id=?", (event_id,))
        if ev is None:
            raise HTTPException(404, "evento no encontrado")
        return ev

    @app.get("/api/alerts")
    def api_alerts(run_id: str = "", rule_id: str = "", severity: str = "", status: str = ""):
        where, params = alert_filters(run_id, rule_id, severity, status)
        rows = store.query(f"SELECT * FROM alerts WHERE {where} ORDER BY timestamp", params)
        for r in rows:
            r["event_ids"] = json.loads(r.pop("event_ids_json"))
            r["evidence"] = json.loads(r.pop("evidence_json"))
        return {"total": len(rows), "items": rows}

    @app.get("/api/alerts/{alert_id}")
    def api_alert(alert_id: str):
        a = store.one("SELECT * FROM alerts WHERE alert_id=?", (alert_id,))
        if a is None:
            raise HTTPException(404, "alerta no encontrada")
        a["event_ids"] = json.loads(a.pop("event_ids_json"))
        a["evidence"] = json.loads(a.pop("evidence_json"))
        return a

    @app.get("/api/runs/{run_id}/metrics")
    def api_run_metrics(run_id: str):
        if store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)) is None:
            raise HTTPException(404, "corrida no encontrada")
        return metrics_mod.compute_all(store, run_id, metrics_mod.context_for(store, run_id))

    @app.get("/healthz")
    def healthz():
        n = store.one("SELECT COUNT(*) n FROM events")["n"]
        return JSONResponse({"status": "ok", "events": n, "live": app.state.live is not None})

    @app.get("/metrics")
    def prometheus():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _chart(windows: list[dict], run: dict, canon: dict, ctx: dict) -> dict | None:
    """Datos para una gráfica SVG de low_rate por ventana (umbral de alerta y referencia)."""
    if not windows:
        return None
    rule = (canon.get("alerts") or {}).get("low_confidence", {})
    ref = (ctx.get("reference") or {}).get("low_rate")
    W, H, pad = 720, 220, 36
    n = len(windows)
    bw = (W - 2 * pad) / n

    def y(v):
        return H - pad - v * (H - 2 * pad)

    bars = []
    for i, w in enumerate(windows):
        rate = w["low_rate"]
        h = 0 if rate is None else rate * (H - 2 * pad)
        bars.append({"x": pad + i * bw + 2, "w": max(bw - 4, 1), "y": H - pad - h, "h": h, "state": w["state"],
                     "label": f"{(w['window_start'] - run['started_at']):.0f}s",
                     "title": f"ventana {i}: n={w['n_eligible']} low={w['n_low']} "
                              f"rate={'—' if rate is None else f'{rate:.2f}'} estado={w['state']}"})
    return {"W": W, "H": H, "pad": pad, "bars": bars,
            "alert_y": y(rule["min_low_rate"]) if "min_low_rate" in rule else None,
            "alert_v": rule.get("min_low_rate"), "ref_y": y(ref) if ref is not None else None, "ref_v": ref,
            "ticks": [{"v": v, "y": y(v)} for v in (0, 0.25, 0.5, 0.75, 1.0)]}
