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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
