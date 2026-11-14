"""Línea de comandos del centro de monitoreo.

Uso: python -m monitoreo.cli <comando> [opciones]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2


def cmd_fetch_model(args: argparse.Namespace) -> int:
    from .model import download_weights, sha256_file

    path = download_weights()
    print(json.dumps({"weights_file": str(path), "sha256": sha256_file(path)}, indent=2))
    return 0


def cmd_model_info(args: argparse.Namespace) -> int:
    from .model import Classifier

    clf = Classifier()
    info = clf.identity()
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_infer(args: argparse.Namespace) -> int:
    from .model import Classifier

    frame = cv2.imread(args.image)
    if frame is None:
        print(f"No se pudo leer la imagen: {args.image}", file=sys.stderr)
        return 2
    clf = Classifier()
    pred = clf.predict(frame)
    out = {
        "image": args.image,
        "model_ref": clf.model_ref,
        "class_id": pred.class_id,
        "class_name": pred.class_name,
        "score": pred.score,
        "top5": pred.top5,
        "preprocess_ms": round(pred.preprocess_ms, 3),
        "inference_ms": round(pred.inference_ms, 3),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _load_source(args: argparse.Namespace):
    """Fuente desde un manifiesto de escenario (--scenario-file) o desde flags."""
    import yaml

    from .config import SourceCfg, resolve

    gaps, scenario, notes = {}, args.scenario, None
    if args.scenario_file:
        spec = yaml.safe_load(resolve(args.scenario_file).read_text(encoding="utf-8"))
        src = SourceCfg(**spec["source"])
        gaps = {int(g["at_seq"]): float(g["seconds"]) for g in spec.get("gaps", [])}
        scenario = scenario or spec.get("scenario")
        notes = spec.get("description")
    else:
        src = SourceCfg(source_id=args.source_id, kind=args.kind, path=args.path, device=args.device,
                        fps=args.fps, loop=args.loop)
    if args.max_frames is not None:
        src.max_frames = args.max_frames
    if args.duration is not None:
        src.duration_s = args.duration
    return src, gaps, scenario or "adhoc", notes


def cmd_run(args: argparse.Namespace) -> int:
    from .config import load_config, resolve
    from .pipeline import Pipeline
    from .store import Store

    cfg = load_config(args.config)
    src, gaps, scenario, notes = _load_source(args)
    store = Store(resolve(args.store))
    factory = None
    try:
        from .rules import RuleEngine  # disponible a partir del hito 3
        if cfg.alerts:
            factory = lambda p: RuleEngine(p.cfg, p.store, p.run_id)  # noqa: E731
    except ImportError:
        pass
    pipe = Pipeline(cfg, src, store, scenario=scenario, gaps=gaps, rule_engine_factory=factory, notes=notes)
    print(f"run_id={pipe.run_id} config_ref={cfg.config_ref} model_ref={pipe.clf.model_ref} store={store.root}")
    try:
        pipe.run()
    except KeyboardInterrupt:
        pipe.stop()
        pipe.wait()
    summary = store.query(
        "SELECT outcome, COUNT(*) n FROM frames WHERE run_id=? GROUP BY outcome", (pipe.run_id,))
    zones = store.query("SELECT COALESCE(zone, status) z, COUNT(*) n FROM events WHERE run_id=? GROUP BY z",
                        (pipe.run_id,))
    alerts = store.query("SELECT rule_id, severity, status FROM alerts WHERE run_id=?", (pipe.run_id,))
    print(json.dumps({"run_id": pipe.run_id, "frames": summary, "events": zones, "alerts": alerts}, indent=2))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Graba la webcam como secuencia de imágenes a fps fijo (fuente reproducible)."""
    import time

    from .config import SourceCfg, resolve
    from .sources import open_source

    out = resolve(args.out)
    out.mkdir(parents=True, exist_ok=True)
    src = SourceCfg(source_id="webcam-grabacion", kind="webcam", device=args.device, fps=args.fps,
                    duration_s=args.seconds)
    n = 0
    print(f"Grabando {args.seconds}s a {args.fps} fps en {out} ... (Ctrl+C para terminar)")
    try:
        for raw in open_source(src):
            if raw.image is not None:
                cv2.imwrite(str(out / f"{args.prefix}{raw.seq:05d}.jpg"), raw.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
                n += 1
    except KeyboardInterrupt:
        pass
    (out / "GRABACION.json").write_text(json.dumps({
        "device": args.device, "fps": args.fps, "frames": n, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2), encoding="utf-8")
    print(f"{n} frames guardados")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Inferencia en vivo de la webcam, solo en consola (no guarda nada). Sirve para encuadrar."""
    from .config import SourceCfg
    from .model import Classifier
    from .sources import open_source

    clf = Classifier()
    src = SourceCfg(source_id="probe", kind="webcam", device=args.device, fps=args.fps, duration_s=args.seconds)
    try:
        for raw in open_source(src):
            if raw.image is None:
                print("fallo de lectura")
                continue
            p = clf.predict(raw.image)
            alt = ", ".join(f"{t['class_name']} {t['score']:.2f}" for t in p.top5[1:3])
            print(f"{raw.seq:4d}  {p.class_name:28s} {p.score:.3f}   ({alt})", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    from . import reference
    from .config import load_config, resolve
    from .store import Store

    store = Store(resolve(args.store))
    run_id = args.run_id or store.one(
        "SELECT run_id FROM runs WHERE scenario='referencia' ORDER BY started_at DESC LIMIT 1")["run_id"]
    if args.action == "propose":
        out = reference.propose(store, run_id)
    else:
        cfg = load_config(args.config)
        out = reference.build(store, run_id, cfg)
        out["written_to"] = reference.write(out, cfg)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _scenario_spec(path: str | None) -> dict | None:
    import yaml

    from .config import resolve

    return yaml.safe_load(resolve(path).read_text(encoding="utf-8")) if path else None


def cmd_metrics(args: argparse.Namespace) -> int:
    from . import metrics
    from .config import load_config, resolve
    from .store import Store

    store = Store(resolve(args.store))
    ctx = metrics.context_for(store, args.run_id, load_config(args.config), _scenario_spec(args.scenario_file))
    res = metrics.compute_all(store, args.run_id, ctx, only_base=args.base)
    if args.inventory:
        metrics.write_inventory(resolve(args.inventory))
    for k, v in res.items():
        val = v["value"]
        print(f"{k:40s} {'BASE' if v['base'] else '    '} {('null' if val is None else f'{val:.4g}'):>10s} "
              f"{v['unit']:16s} n={v['n']}  {v['status']}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from . import metrics
    from .config import load_config, resolve
    from .export import export_run
    from .store import Store

    store = Store(resolve(args.store))
    ctx = metrics.context_for(store, args.run_id, load_config(args.config), _scenario_spec(args.scenario_file))
    doc = export_run(store, args.run_id, resolve(args.out), ctx)
    print(f"Exportado {args.run_id} -> {args.out} ({len(doc['metrics'])} métricas)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import threading

    import uvicorn

    from .config import load_config, resolve
    from .portal import create_app
    from .store import Store

    live = None
    if args.live:
        from .pipeline import Pipeline
        from .rules import RuleEngine

        cfg = load_config(args.config)
        src, gaps, scenario, notes = _load_source(args)
        pstore = Store(resolve(args.store))
        live = Pipeline(cfg, src, pstore, scenario=scenario, gaps=gaps, notes=notes,
                        rule_engine_factory=(lambda p: RuleEngine(p.cfg, p.store, p.run_id)) if cfg.alerts else None)
        threading.Thread(target=live.run, daemon=True, name="pipeline").start()
        print(f"Pipeline en vivo: {live.run_id}")
    app = create_app(resolve(args.store), live=live)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    if live is not None and not live.finished:
        live.stop()
        live.wait()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="monitoreo")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch-model", help="Descarga y verifica los pesos del modelo")
    s.set_defaults(func=cmd_fetch_model)

    s = sub.add_parser("model-info", help="Imprime identidad, clases, preprocesamiento y entorno")
    s.add_argument("--output", help="Ruta JSON donde guardar el manifiesto")
    s.set_defaults(func=cmd_model_info)

    s = sub.add_parser("infer", help="Inferencia real sobre una imagen")
    s.add_argument("--image", required=True)
    s.set_defaults(func=cmd_infer)

    s = sub.add_parser("run", help="Ejecuta el pipeline de monitoreo sobre una fuente")
    s.add_argument("--config", default="config/monitoreo.yaml")
    s.add_argument("--store", default="var/store", help="Carpeta con monitoreo.db y capturas/")
    s.add_argument("--scenario-file", help="Manifiesto YAML del escenario (fuente, huecos, descripción)")
    s.add_argument("--scenario", help="Nombre del escenario (por defecto, el del manifiesto)")
    s.add_argument("--kind", choices=["sequence", "video", "webcam"], default="sequence")
    s.add_argument("--path", help="Carpeta de imágenes o archivo de video")
    s.add_argument("--device", type=int, default=0)
    s.add_argument("--source-id", default="fuente-local-01")
    s.add_argument("--fps", type=float, default=4.0)
    s.add_argument("--loop", action="store_true")
    s.add_argument("--max-frames", type=int)
    s.add_argument("--duration", type=float, help="Segundos máximos de captura")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("record", help="Graba la webcam como secuencia de imágenes")
    s.add_argument("--out", required=True)
    s.add_argument("--device", type=int, default=0)
    s.add_argument("--fps", type=float, default=4.0)
    s.add_argument("--seconds", type=float, default=60.0)
    s.add_argument("--prefix", default="frame_")
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("probe", help="Muestra clase y score en vivo desde la webcam (no guarda)")
    s.add_argument("--device", type=int, default=0)
    s.add_argument("--fps", type=float, default=2.0)
    s.add_argument("--seconds", type=float, default=30.0)
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("reference", help="Propone umbrales o construye la referencia desde una corrida")
    s.add_argument("action", choices=["propose", "build"])
    s.add_argument("--store", default="var/store")
    s.add_argument("--run-id", help="Por defecto, la última corrida con escenario 'referencia'")
    s.add_argument("--config", default="config/monitoreo.yaml")
    s.set_defaults(func=cmd_reference)

    for name, fn, hlp in (("metrics", cmd_metrics, "Calcula las métricas G1–G7 de una corrida"),
                          ("export", cmd_export, "Exporta una corrida a CSV/JSONL/JSON recalculables")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--store", default="var/store")
        s.add_argument("--run-id", required=True)
        s.add_argument("--config", default="config/monitoreo.yaml")
        s.add_argument("--scenario-file", help="Manifiesto del escenario (para métricas con condición declarada)")
        if name == "metrics":
            s.add_argument("--base", action="store_true", help="Solo métricas base")
            s.add_argument("--inventory", help="Escribe el inventario de definiciones en esta ruta JSON")
        else:
            s.add_argument("--out", required=True)
        s.set_defaults(func=fn)

    s = sub.add_parser("serve", help="Inicia el portal (opcionalmente con un pipeline en vivo)")
    s.add_argument("--store", default="evidencias/almacen")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--live", action="store_true", help="Ejecuta además un pipeline sobre la fuente indicada")
    s.add_argument("--config", default="config/monitoreo.yaml")
    s.add_argument("--scenario-file")
    s.add_argument("--scenario")
    s.add_argument("--kind", choices=["sequence", "video", "webcam"], default="webcam")
    s.add_argument("--path")
    s.add_argument("--device", type=int, default=0)
    s.add_argument("--source-id", default="webcam-escritorio-01")
    s.add_argument("--fps", type=float, default=4.0)
    s.add_argument("--loop", action="store_true")
    s.add_argument("--max-frames", type=int)
    s.add_argument("--duration", type=float)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
