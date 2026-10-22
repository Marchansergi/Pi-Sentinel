"""Utilidades de prueba.

Todo es sintético: ningún test usa fotos de personas. Las pruebas de reconocimiento con embeddings
inventados verifican la LÓGICA (umbrales, estados), no la precisión real del modelo.
"""
from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app.config import PROJECT_ROOT, Paths
from app.domain import IdentityStatus
from app.face_recognition import EMBED_DIM, FaceAnalysis, FaceObservation
from app.person_detector import DetectionResult, PersonBox


def unit(seed: int) -> np.ndarray:
    v = np.random.RandomState(seed).randn(EMBED_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def noisy(base: np.ndarray, amount: float, seed: int = 0) -> np.ndarray:
    # `amount` es la norma relativa del ruido respecto al vector unitario base
    v = base + amount * np.random.RandomState(seed).randn(EMBED_DIM).astype(np.float32) / np.sqrt(EMBED_DIM)
    return v / np.linalg.norm(v)


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths(data_dir=tmp_path / "data", models_dir=PROJECT_ROOT / "models", builtin_sounds_dir=tmp_path / "sounds")


def models_present() -> bool:
    return all((PROJECT_ROOT / "models" / n).is_file() for n in (
        "object_detection_yolox_2022nov.onnx", "face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"))


requires_models = pytest.mark.skipif(not models_present(), reason="modelos no descargados (scripts/download_models.py)")


# ------------------------------------------------------------------ cámara simulada


def blank_frame(w=640, h=480, value=100) -> np.ndarray:
    return np.full((h, w, 3), value, np.uint8)


class ScriptedSource:
    """Origen de vídeo controlable. `frames` se reparte cíclicamente; None simula lectura fallida."""

    def __init__(self, owner: "SourceFactory") -> None:
        self.owner = owner
        self.is_open = False
        self._i = 0

    def open(self):
        self.owner.opens += 1
        if self.owner.fail_opens > 0:
            self.owner.fail_opens -= 1
            raise RuntimeError("cámara no disponible")
        self.is_open = True

    def read(self, decode=True):
        time.sleep(0.002)
        if self.owner.fail_reads:
            return False, None
        item = self.owner.frames[self._i % len(self.owner.frames)]
        self._i += 1
        return True, (item() if callable(item) else item) if decode else None

    def close(self):
        if self.is_open:
            self.owner.closes += 1
        self.is_open = False


class SourceFactory:
    def __init__(self, frames=None, fail_opens=0):
        self.frames = frames or [blank_frame()]
        self.fail_opens = fail_opens
        self.fail_reads = False
        self.opens = 0
        self.closes = 0
        self.sources: list[ScriptedSource] = []

    def __call__(self, cfg):
        s = ScriptedSource(self)
        self.sources.append(s)
        return s


# ------------------------------------------------------------------ visión simulada


class StubDetector:
    """Devuelve las personas que se le indiquen; permite simular errores del modelo."""

    def __init__(self):
        self.persons = []
        self.fail = False
        self.calls = 0
        self.loaded = True
        self.last_error = None

    def detect(self, frame, confidence=0.5, nms=0.45, input_size=416, min_area_ratio=0.0):
        self.calls += 1
        if self.fail:
            return DetectionResult(False, (), "modelo roto", 1.0)
        return DetectionResult(True, tuple(self.persons), None, 1.0)


class StubFaces:
    """Motor facial falso: devuelve las caras ya clasificadas que se le indiquen."""

    def __init__(self):
        self.faces = []
        self.fail = False
        self.calls = 0
        self.loaded = True
        self.last_error = None

    def analyze(self, frame, gallery, cfg, max_embeds=3):
        self.calls += 1
        if self.fail:
            return FaceAnalysis(False, (), "motor facial roto", 1.0)
        return FaceAnalysis(True, tuple(self.faces), None, 1.0)

    def detect(self, frame):
        return []

    sample = None  # (embedding, FaceQuality | None, motivo)

    def extract_sample(self, frame, cfg):
        sample = self.sample
        return sample if sample is not None else (None, None, "No se detecta ninguna cara.")


def person_box(x1=100, y1=50, x2=300, y2=450, conf=0.9):
    return PersonBox(x1, y1, x2, y2, conf)


def face_obs(status: IdentityStatus, name=None, sim=None, box=(150, 80, 250, 200), reasons=()):
    return FaceObservation(box, status, name, 1 if name else None, sim, tuple(reasons), 0.95)


# ------------------------------------------------------------------ audio simulado


class FakeAudio:
    def __init__(self, fail_check=False, fail_play=False, delay=0.0):
        self.fail_check, self.fail_play, self.delay = fail_check, fail_play, delay
        self.played = []

    def check(self, player="auto"):
        from app.sound_alert import AudioError

        if self.fail_check:
            raise AudioError("No se encontró ningún reproductor de audio")
        return "fake"

    def play(self, path, stop, device="", player="auto"):
        from app.sound_alert import AudioError

        self.targets = getattr(self, "targets", []) + [(player, device)]
        if self.fail_play:
            raise AudioError("sin salida de audio")
        self.played.append(Path(path).name)
        stop.wait(self.delay)


# ------------------------------------------------------------------ servidor ntfy simulado


class MockNtfy:
    """Servidor HTTP local que registra las peticiones y responde según un guion."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: list = []  # elementos: int (status) | ("sleep", seg) | (status, {headers})
        self.default_status = 200
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                item = outer.script.pop(0) if outer.script else outer.default_status
                headers = {}
                if isinstance(item, tuple) and item[0] == "sleep":
                    time.sleep(item[1])
                    item = 200
                elif isinstance(item, tuple):
                    item, headers = item
                self.send_response(item)
                for k, v in headers.items():
                    self.send_header(k, v)
                payload = json.dumps({"id": "x", "error": "boom"} if item >= 400 else {"id": "x"}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ntfy_server():
    s = MockNtfy()
    yield s
    s.close()


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
