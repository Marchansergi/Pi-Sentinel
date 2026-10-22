"""Punto de entrada: python -m app.main [run|set-password|check|benchmark]."""
from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, load_dotenv, resolve_paths
from .security import BindError, RedactingFilter, hash_password, validate_bind, validate_new_password

log = logging.getLogger("pi_sentinel")


def setup_logging(paths, secrets=None, config=None) -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(paths.data_dir, 0o700)
    except OSError:
        pass

    def extras():
        vals = list(secrets.all_secret_values()) if secrets else []
        if config:
            vals.append(config.get().ntfy.topic)
        return vals

    flt = RedactingFilter(extras)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    logfile = paths.logs_dir / "sentinel.log"
    fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    sh = logging.StreamHandler()
    root = logging.getLogger()
    root.handlers.clear()
    for h in (fh, sh):
        h.setFormatter(fmt)
        h.addFilter(flt)
        root.addHandler(h)
    root.setLevel(level)
    try:
        os.chmod(logfile, 0o600)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)  # evita registrar URLs de peticiones
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def cmd_run(args) -> int:
    import uvicorn

    from .service import SentinelService
    from .web import create_app

    paths = resolve_paths()
    service = SentinelService(paths)
    setup_logging(paths, service.secrets, service.config)
    web = service.config.get().web
    host = os.environ.get("PI_SENTINEL_HOST", args.host or web.host)
    port = int(os.environ.get("PI_SENTINEL_PORT", args.port or web.port))
    allow_lan = _env_bool("PI_SENTINEL_ALLOW_LAN", web.allow_lan)
    try:
        validate_bind(host, allow_lan, bool(service.secrets.password_hash()))
    except BindError as exc:
        log.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    app = create_app(service, host=host, port=port, allow_lan=allow_lan, extra_hosts=os.environ.get("PI_SENTINEL_ALLOWED_HOSTS", ""), on_shutdown=service.shutdown)
    cert, key = os.environ.get("PI_SENTINEL_TLS_CERT"), os.environ.get("PI_SENTINEL_TLS_KEY")
    scheme = "https" if cert and key else "http"
    service.start()
    log.info("Pi Sentinel escuchando en %s://%s:%d (LAN %s)", scheme, host, port, "habilitada" if allow_lan else "deshabilitada")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False,
                    ssl_certfile=cert if (cert and key) else None, ssl_keyfile=key if (cert and key) else None)
    finally:
        service.shutdown()
    return 0


def cmd_set_password(args) -> int:
    from .security import SecretStore

    paths = resolve_paths()
    secrets = SecretStore(paths.secrets_path)
    pw = getpass.getpass("Nueva contraseña de administración: ")
    if pw != getpass.getpass("Repítela: "):
        print("Las contraseñas no coinciden.", file=sys.stderr)
        return 1
    problem = validate_new_password(pw)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    secrets.set("admin_password_hash", hash_password(pw))
    print("Contraseña guardada (hash scrypt en data/secrets.json, permisos 0600). Reinicia el servicio.")
    return 0


def _pi_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_text().strip("\x00\n ")
    except OSError:
        return "no es una Raspberry Pi (o no se pudo leer /proc/device-tree/model)"


def _mem_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def cmd_check(args) -> int:
    """Diagnóstico de compatibilidad. Código de salida ≠ 0 si falta algo imprescindible."""
    from .camera import list_cameras
    from .service import FACE_DET_MODEL, FACE_REC_MODEL, PERSON_MODEL
    from .sound_alert import PLAYERS

    paths = resolve_paths()
    problems = 0
    print(f"Sistema:       {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Modelo:        {_pi_model()}")
    print(f"Python:        {platform.python_version()}")
    ram = _mem_mb()
    print(f"RAM:           {ram} MB" if ram else "RAM:           desconocida")
    if ram and ram < 1800:
        print("  AVISO: poca RAM; usa input_size=320 y analysis_fps bajo.")
    if platform.machine() not in ("aarch64", "x86_64", "arm64"):
        print("  AVISO: arquitectura de 32 bits; Raspberry Pi OS de 64 bits es lo recomendado.")
    try:
        import cv2

        ok = hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF")
        print(f"OpenCV:        {cv2.__version__} (FaceDetectorYN/FaceRecognizerSF: {'sí' if ok else 'NO'})")
        problems += 0 if ok else 1
    except ImportError:
        print("OpenCV:        NO instalado")
        problems += 1
    for name in (PERSON_MODEL, FACE_DET_MODEL, FACE_REC_MODEL):
        present = (paths.models_dir / name).is_file()
        print(f"Modelo {name}: {'OK' if present else 'FALTA (scripts/download_models.py)'}")
        problems += 0 if present else 1
    cams = list_cameras()
    print("Cámaras:       " + (", ".join(c["label"] for c in cams) if cams else "ninguna detectada"))
    players = [e for e, _ in PLAYERS if shutil.which(e)]
    print("Audio:         " + (", ".join(players) if players else "sin reproductor (instala alsa-utils)"))
    try:
        import picamera2  # noqa: F401

        print("Picamera2:     disponible")
    except Exception:
        print("Picamera2:     no disponible (solo cámaras USB/V4L2)")
    print(f"Datos:         {paths.data_dir}")
    return 1 if problems else 0


def cmd_benchmark(args) -> int:
    """Mide latencias de los modelos en ESTE equipo (ejecútalo en la Raspberry Pi para datos reales)."""
    import cv2
    import numpy as np

    from .face_recognition import FaceEngine
    from .person_detector import PersonDetector
    from .service import FACE_DET_MODEL, FACE_REC_MODEL, PERSON_MODEL

    paths = resolve_paths()
    img = cv2.imread(args.image) if args.image else None
    if img is None:
        img = np.random.RandomState(0).randint(0, 255, (480, 640, 3), dtype=np.uint8)
        print("(imagen sintética aleatoria; usa --image para una escena real)")
    img = cv2.resize(img, (640, 480))
    print(f"Sistema: {platform.machine()} / {_pi_model()} / OpenCV {cv2.__version__} / {os.cpu_count()} CPU")
    det = PersonDetector(paths.models_dir / PERSON_MODEL)
    for size in (320, 416, 640):
        det.detect(img, input_size=size)  # calentamiento
        t = []
        for _ in range(args.runs):
            t0 = time.perf_counter()
            r = det.detect(img, input_size=size)
            t.append((time.perf_counter() - t0) * 1000)
            if not r.ok:
                print(f"  YOLOX {size}: ERROR {r.error}")
                break
        else:
            print(f"  YOLOX input {size}: mediana {np.median(t):.0f} ms, p95 {np.percentile(t, 95):.0f} ms")
    eng = FaceEngine(paths.models_dir / FACE_DET_MODEL, paths.models_dir / FACE_REC_MODEL)
    eng.load()
    t = []
    for _ in range(args.runs):
        t0 = time.perf_counter()
        eng.detect(img)
        t.append((time.perf_counter() - t0) * 1000)
    print(f"  YuNet detección de caras 640x480: mediana {np.median(t):.0f} ms, p95 {np.percentile(t, 95):.0f} ms")
    print("  (SFace se mide en uso real: depende del número de caras)")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    p = argparse.ArgumentParser(prog="pi-sentinel", description="Vigilancia local con reconocimiento facial")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="Inicia el servicio (por defecto)")
    r.add_argument("--host")
    r.add_argument("--port", type=int)
    r.set_defaults(fn=cmd_run)
    sub.add_parser("set-password", help="Define la contraseña de administración").set_defaults(fn=cmd_set_password)
    sub.add_parser("check", help="Comprueba compatibilidad y dependencias").set_defaults(fn=cmd_check)
    b = sub.add_parser("benchmark", help="Mide el rendimiento de los modelos")
    b.add_argument("--image")
    b.add_argument("--runs", type=int, default=10)
    b.set_defaults(fn=cmd_benchmark)
    args = p.parse_args(argv)
    if not getattr(args, "fn", None):
        args = p.parse_args(["run", *(argv or [])])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
