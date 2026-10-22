#!/usr/bin/env python3
"""Descarga los modelos ONNX y verifica su SHA-256 antes de aceptarlos.

Fuente: repositorio oficial opencv/opencv_zoo (ficheros LFS servidos por media.githubusercontent.com).
Si un hash no coincide, el fichero se rechaza y se borra. Solo usa la biblioteca estándar.

Licencias de los modelos:
  - YuNet (detección de caras)      MIT
  - SFace (reconocimiento facial)   Apache-2.0
  - YOLOX (detección de personas)   Apache-2.0
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    "object_detection_yolox_2022nov.onnx": (
        f"{BASE}/object_detection_yolox/object_detection_yolox_2022nov.onnx",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=os.environ.get("PI_SENTINEL_MODELS_DIR") or str(Path(__file__).resolve().parent.parent / "models"))
    ap.add_argument("--verify-only", action="store_true", help="No descarga: solo comprueba los hashes")
    args = ap.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name, (url, digest) in MODELS.items():
        target = dest / name
        if target.is_file() and sha256(target) == digest:
            print(f"OK       {name} (hash verificado)")
            continue
        if args.verify_only:
            print(f"FALTA/MAL {name}")
            rc = 1
            continue
        print(f"Descargando {name} …")
        tmp = target.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
        except Exception as exc:
            print(f"ERROR    no se pudo descargar {name}: {exc}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            rc = 1
            continue
        if sha256(tmp) != digest:
            print(f"ERROR    el hash de {name} NO coincide; fichero descartado", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            rc = 1
            continue
        tmp.replace(target)
        print(f"OK       {name} descargado y verificado")
    return rc


if __name__ == "__main__":
    sys.exit(main())
