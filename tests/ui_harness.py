"""Servidor de desarrollo con cámara/visión SIMULADAS para revisar la interfaz sin hardware.

No forma parte de la aplicación: `python tests/ui_harness.py <puerto> <directorio_datos>`.
"""
import sys
from pathlib import Path

import numpy as np
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Paths
from app.domain import IdentityStatus as S
from app.face_recognition import FaceQuality
from app.service import FACE_DET_MODEL, FACE_REC_MODEL, PERSON_MODEL, SentinelService
from app.web import create_app
from conftest import FakeAudio, SourceFactory, StubDetector, StubFaces, face_obs, noisy, person_box, unit


def scene_frame():
    img = np.full((480, 640, 3), 60, np.uint8)
    img[:, :, 1] = np.linspace(40, 120, 640, dtype=np.uint8)[None, :]
    return img


def main():
    port, data = int(sys.argv[1]), Path(sys.argv[2])
    models = data / "models"
    models.mkdir(parents=True, exist_ok=True)
    for n in (PERSON_MODEL, FACE_DET_MODEL, FACE_REC_MODEL):
        (models / n).write_bytes(b"x")
    faces = StubFaces()
    counter = iter(range(10**6))

    class Samples:  # cada captura devuelve un embedding ligeramente distinto del mismo «rostro» sintético
        def __bool__(self):
            return True

        def __getitem__(self, i):
            return (noisy(unit(1), 0.05, next(counter)), FaceQuality(True, (), 100.0, 120.0, 0.0, 120), None)[i]

    class SampleProp(StubFaces):
        yaws = iter([0.0, 0.0, 0.4, -0.4, 0.0, 0.0] + [0.0] * 50)

        @property
        def sample(self):
            return (noisy(unit(1), 0.05, next(counter)), FaceQuality(True, (), 100.0, 120.0, next(self.yaws), 120), None)

        @sample.setter
        def sample(self, v):
            pass

    faces = SampleProp()
    faces.faces = [face_obs(S.UNKNOWN, sim=0.1)]  # siempre hay un desconocido delante de la «cámara»
    det = StubDetector()
    det.persons = [person_box()]
    svc = SentinelService(Paths(data / "d", models, data / "s"), source_factory=SourceFactory([scene_frame]), detector=det, faces=faces, audio_backend=FakeAudio())
    svc.config.update({'tracking': {'confirm_seconds': 1.0, 'min_observations': 2, 'alert_cooldown_seconds': 0.0}, 'detection': {'motion_gate': False, 'analysis_fps': 5.0}})
    svc.start()
    uvicorn.run(create_app(svc, port=port), host="127.0.0.1", port=port, log_level="warning")


main()
