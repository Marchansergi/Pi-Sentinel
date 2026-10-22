"""Integración del servicio con cámara, visión y audio simulados (sin hardware ni modelos reales)."""
import time

import numpy as np
import pytest

from app.config import Paths
from app.domain import IdentityStatus as S
from app.face_recognition import FaceQuality
from app.service import (FACE_DET_MODEL, FACE_REC_MODEL, PERSON_MODEL, Mode, SentinelService, ServiceError,
                         build_observations)
from conftest import (FakeAudio, SourceFactory, StubDetector, StubFaces, blank_frame, face_obs, free_port,
                      noisy, person_box, unit)

FAST = {"tracking": {"confirm_seconds": 0.0, "min_observations": 2, "min_authorized_observations": 2,
                     "alert_cooldown_seconds": 0.0, "exit_seconds": 5.0},
        "detection": {"motion_gate": False, "analysis_fps": 10.0},
        "alarm": {"min_interval_seconds": 0.0},
        "entry": {"enabled": False}}  # el sonido de entrada se prueba aparte (test_entry_chime.py)


@pytest.fixture
def paths(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    for n in (PERSON_MODEL, FACE_DET_MODEL, FACE_REC_MODEL):
        (models / n).write_bytes(b"x")  # los motores reales se sustituyen por simulados
    return Paths(tmp_path / "data", models, tmp_path / "sounds")


class Env:
    def __init__(self, paths, ntfy_url=None):
        self.src = SourceFactory()
        self.det, self.faces, self.audio = StubDetector(), StubFaces(), FakeAudio()
        self.svc = SentinelService(paths, source_factory=self.src, detector=self.det, faces=self.faces, audio_backend=self.audio)
        patch = dict(FAST)
        if ntfy_url:
            patch["ntfy"] = {"enabled": True, "server": ntfy_url, "topic": "pisentinel-" + "a" * 30, "timeout_seconds": 1.0}
        self.svc.config.update(patch)

    def with_owner(self):
        p = self.svc.people.create("Ana")
        for i in range(5):
            self.svc.people.add_sample(p.id, noisy(unit(1), 0.05, i), 0.9)
        return p

    def cycle(self, n=1):
        for _ in range(n):
            self.svc._process(blank_frame(), self.svc.config.get())

    def set_scene(self, *faces, persons=1):
        self.det.persons = [person_box(100 + i * 400, 50, 300 + i * 400, 450) for i in range(persons)]
        self.faces.faces = list(faces)

    def events(self):
        return self.svc.db.query("SELECT * FROM events")

    def notifs(self):
        return self.svc.db.query("SELECT * FROM notifications WHERE kind='alert'")


@pytest.fixture
def env(paths):
    e = Env(paths)
    yield e
    e.svc.shutdown()


def test_active_mode_refused_without_authorized_profile(env):
    with pytest.raises(ServiceError) as ei:
        env.svc.set_mode(Mode.ACTIVE)
    assert ei.value.status_code == 409 and "perfil" in str(ei.value)
    assert env.svc.mode == Mode.STOPPED


def test_active_mode_refused_when_models_missing(paths):
    (paths.models_dir / PERSON_MODEL).unlink()
    e = Env(paths)
    e.with_owner()
    try:
        with pytest.raises(ServiceError) as ei:
            e.svc.set_mode(Mode.ACTIVE)
        assert ei.value.status_code == 503 and "download_models" in str(ei.value)
    finally:
        e.svc.shutdown()


def test_unknown_person_confirms_one_event_alarm_and_notification(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.set_scene(face_obs(S.UNKNOWN, sim=0.05))
    env.cycle(2)
    assert len(env.events()) == 1 and env.events()[0]["identity_status"] == "UNKNOWN"
    env.svc.alarm.join()
    assert len(env.audio.played) == 1
    n = env.notifs()
    assert len(n) == 0  # ntfy desactivado por defecto: el evento lo refleja
    assert env.events()[0]["notify_state"] == "DISABLED"


def test_no_repeated_alerts_during_continuous_presence(paths, ntfy_server):
    e = Env(paths, ntfy_server.url)
    try:
        e.with_owner()
        e.svc.set_mode(Mode.ACTIVE)
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
        e.cycle(40)
        e.svc.alarm.join()
        assert len(e.events()) == 1 and len(e.notifs()) == 1 and len(e.audio.played) == 1
        e.svc.notifier.process_due()
        assert len(ntfy_server.requests) == 1
        body = ntfy_server.requests[0]["body"]
        assert "desconocida" in body["title"].lower() and "Estado: desconocido" in body["message"]
        assert "Ana" not in body["message"]
    finally:
        e.svc.shutdown()


def test_reentry_after_exit_creates_second_event_and_notification(paths, ntfy_server):
    e = Env(paths, ntfy_server.url)
    try:
        e.with_owner()
        e.svc.set_mode(Mode.ACTIVE)
        e.svc.config.update({"tracking": {"exit_seconds": 1.0}})
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
        e.cycle(3)
        e.set_scene(persons=0)
        e.cycle(1)
        time.sleep(1.2)
        e.cycle(1)  # la presencia expira
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
        e.cycle(3)
        assert len(e.events()) == 2 and len(e.notifs()) == 2
    finally:
        e.svc.shutdown()


def test_authorized_person_does_not_alarm(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.set_scene(face_obs(S.AUTHORIZED, name="Ana", sim=0.8))
    env.cycle(10)
    env.svc.alarm.join()
    assert env.events() == [] and env.audio.played == []


def test_authorized_and_unknown_together_alert_for_unknown(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.set_scene(face_obs(S.AUTHORIZED, name="Ana", sim=0.8, box=(150, 80, 250, 200)),
                  face_obs(S.UNKNOWN, sim=0.02, box=(550, 80, 650, 200)), persons=2)
    env.cycle(4)
    st = [r["identity_status"] for r in env.events()]
    assert st == ["UNKNOWN"]


def test_person_without_face_is_unverified_and_not_alerted_by_default(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.svc.config.update({"tracking": {"unverified_timeout_seconds": 1.0}})
    env.set_scene()  # persona visible, ninguna cara
    env.cycle(2)
    time.sleep(1.1)
    env.cycle(2)
    ev = env.events()
    assert [r["identity_status"] for r in ev] == ["UNVERIFIED"]
    env.svc.alarm.join()
    assert env.audio.played == []  # política por defecto: solo registrar


def test_detector_error_is_not_treated_as_empty_room(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.set_scene(face_obs(S.UNKNOWN, sim=0.05))
    env.det.fail = True
    env.cycle(5)
    assert env.events() == []
    c = env.svc.component_status()["detector"]
    assert c["state"] == "error" and "roto" in c["message"]
    env.det.fail = False
    env.cycle(3)
    assert len(env.events()) == 1 and env.svc.component_status()["detector"]["state"] == "ok"


def test_face_engine_error_does_not_break_surveillance(env):
    env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.set_scene(face_obs(S.UNKNOWN))
    env.faces.fail = True
    env.cycle(3)  # sigue procesando personas sin rostro
    assert env.svc.component_status()["face"]["state"] == "error"
    assert env.svc.tracker.snapshot()


def test_ntfy_down_does_not_stop_local_surveillance(paths):
    e = Env(paths, f"http://127.0.0.1:{free_port()}")
    try:
        e.with_owner()
        e.svc.set_mode(Mode.ACTIVE)
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
        e.cycle(3)
        e.svc.notifier.process_due()  # intenta enviar y falla
        e.svc.alarm.join()
        assert len(e.events()) == 1 and len(e.audio.played) == 1  # alarma y evento local intactos
        assert e.svc.notifier.status()["pending"] == 1 and e.svc.notifier.status()["state"] == "error"
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05), face_obs(S.UNKNOWN, box=(550, 80, 650, 200)), persons=2)
        e.cycle(4)  # la vigilancia continúa tras el fallo
        assert len(e.events()) == 2  # la 1.ª presencia continúa; el 2.º desconocido añade un evento
        assert e.svc.mode == Mode.ACTIVE
    finally:
        e.svc.shutdown()


def test_audio_failure_does_not_stop_notification(paths, ntfy_server):
    e = Env(paths, ntfy_server.url)
    e.audio.fail_check = True
    try:
        e.with_owner()
        e.svc.set_mode(Mode.ACTIVE)
        e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
        e.cycle(3)
        assert e.events()[0]["alarm_state"] == "ERROR" and len(e.notifs()) == 1
    finally:
        e.svc.shutdown()


def test_test_mode_has_no_side_effects_but_reports_what_would_happen(env):
    env.with_owner()
    env.svc.set_mode(Mode.TEST)
    env.set_scene(face_obs(S.UNKNOWN, sim=0.05))
    env.cycle(3)
    env.svc.alarm.join()
    assert env.events() == [] and env.audio.played == []
    te = env.svc.status()["test_events"]
    assert te and te[0]["action"] == "would_alert"


def test_surveillance_drops_to_test_mode_if_profiles_disappear(env):
    p = env.with_owner()
    env.svc.set_mode(Mode.ACTIVE)
    env.svc.people.delete_person(p.id)
    env.set_scene(face_obs(S.UNKNOWN))
    env.cycle(3)
    assert env.svc.mode == Mode.TEST and env.events() == [] and "perfiles" in env.svc.status()["mode_note"]


def test_mode_persists_but_test_mode_does_not(paths):
    e = Env(paths)
    e.with_owner()
    e.svc.set_mode(Mode.ACTIVE)
    e.svc.shutdown()
    e2 = Env(paths)
    try:
        e2.svc.start()
        assert e2.svc.mode == Mode.ACTIVE  # se reanuda
        e2.svc.set_mode(Mode.TEST)
    finally:
        e2.svc.shutdown()
    e3 = Env(paths)
    try:
        e3.svc.start()
        assert e3.svc.mode == Mode.STOPPED
    finally:
        e3.svc.shutdown()


def test_threaded_loop_end_to_end_and_clean_restart_frees_camera(paths):
    e = Env(paths)
    e.with_owner()
    e.set_scene(face_obs(S.UNKNOWN, sim=0.05))
    e.svc.start()
    e.svc.set_mode(Mode.ACTIVE)
    end = time.time() + 6
    while time.time() < end and not e.events():
        time.sleep(0.05)
    assert len(e.events()) == 1
    assert e.svc.camera.running
    e.svc.shutdown()
    assert not e.svc.camera.running
    assert e.src.opens == e.src.closes >= 1  # la cámara queda liberada
    e2 = Env(paths)  # reinicio sobre los mismos datos
    try:
        e2.set_scene(face_obs(S.AUTHORIZED, name="Ana", sim=0.8))
        e2.svc.start()
        end = time.time() + 5
        while time.time() < end and e2.svc.camera.latest() is None:
            time.sleep(0.05)
        assert e2.svc.camera.latest() is not None
    finally:
        e2.svc.shutdown()
    assert e2.src.opens == e2.src.closes


def test_camera_off_when_stopped_and_no_preview(env):
    env.svc.start()
    time.sleep(0.4)
    assert not env.svc.camera.running
    env.svc.touch_preview()
    end = time.time() + 3
    while time.time() < end and not env.svc.camera.running:
        time.sleep(0.05)
    assert env.svc.camera.running


def test_analysis_thread_survives_exceptions(env):
    env.with_owner()
    env.set_scene(face_obs(S.UNKNOWN))
    calls = {"n": 0}
    orig = env.det.detect

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("explosión inesperada")
        return orig(*a, **k)

    env.det.detect = flaky
    env.svc.start()
    env.svc.set_mode(Mode.ACTIVE)
    end = time.time() + 8
    while time.time() < end and not env.events():
        time.sleep(0.05)
    assert env.events(), "el hilo de análisis debe recuperarse tras excepciones"


# ------------------------------------------------------------------ registro de muestras


GOOD_Q = FaceQuality(True, (), 100.0, 120.0, 0.0, 120)


def test_capture_sample_flow(env):
    p = env.svc.people.create("Ana")
    env.faces.sample = (None, None, "No se detecta ninguna cara.")
    assert env.svc.capture_sample(p.id)["ok"] is False
    env.faces.sample = (None, FaceQuality(False, ("imagen borrosa",), 1, 100, 0, 100), "Muestra rechazada: imagen borrosa")
    assert "borrosa" in env.svc.capture_sample(p.id)["reason"]
    for i in range(5):
        env.faces.sample = (noisy(unit(1), 0.05, i), GOOD_Q, None)
        r = env.svc.capture_sample(p.id)
    assert r["ok"] and r["samples"] == 5 and r["ready"]


def test_capture_pose_checks_and_profile_contamination_guard(env):
    p = env.svc.people.create("Ana")
    env.faces.sample = (unit(1), FaceQuality(True, (), 100, 120, 0.5, 120), None)
    assert "Mira de frente" in env.svc.capture_sample(p.id, "frontal")["reason"]
    env.faces.sample = (unit(1), GOOD_Q, None)
    assert "Gira" in env.svc.capture_sample(p.id, "lateral")["reason"]
    for i in range(3):
        env.faces.sample = (noisy(unit(1), 0.05, i), GOOD_Q, None)
        assert env.svc.capture_sample(p.id)["ok"]
    env.faces.sample = (unit(50), GOOD_Q, None)  # otra persona intenta entrar en el perfil
    r = env.svc.capture_sample(p.id)
    assert not r["ok"] and "no se parece" in r["reason"]


def test_calibration_advice(env):
    import json
    now = time.time()
    env.svc.db.kv_set("calibration_genuine", json.dumps({"n": 10, "min": 0.6, "p10": 0.65, "mean": 0.8, "max": 0.9, "at": now}))
    assert env.svc.calibration_summary()["suggested_threshold"] is None
    env.svc.db.kv_set("calibration_impostor", json.dumps({"n": 10, "min": 0.0, "p10": 0.02, "mean": 0.1, "max": 0.25, "at": now}))
    s = env.svc.calibration_summary()
    assert s["suggested_threshold"] == pytest.approx(0.45, abs=0.01)
    env.svc.db.kv_set("calibration_impostor", json.dumps({"n": 10, "min": 0.0, "p10": 0.02, "mean": 0.5, "max": 0.7, "at": now}))
    s = env.svc.calibration_summary()
    assert s["suggested_threshold"] is None and "solapan" in s["advice"]


# ------------------------------------------------------------------ varios


def test_build_observations_associates_faces_with_people():
    people = [person_box(0, 0, 200, 400), person_box(300, 0, 500, 400)]
    faces = [face_obs(S.AUTHORIZED, "Ana", 0.9, box=(50, 30, 150, 150)), face_obs(S.UNKNOWN, box=(700, 30, 780, 120))]
    obs = build_observations(people, faces)
    assert len(obs) == 3  # 2 personas + 1 cara suelta
    assert obs[0].face_status is S.AUTHORIZED and obs[1].face_status is None and obs[2].face_status is S.UNKNOWN


def test_preview_is_jpeg_and_nothing_is_written_to_disk(env):
    env.svc.start()
    jpg = env.svc.preview_jpeg()
    assert jpg[:2] == b"\xff\xd8"
    chk = env.svc.camera_check()
    assert chk["ok"] and chk["person_detector"]["ok"]
    images = [p for p in env.svc.paths.data_dir.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".mp4", ".avi")]
    assert images == []  # privacidad: nada de fotos ni vídeo por defecto


def test_camera_error_surfaces_in_status_and_does_not_crash(paths):
    e = Env(paths)
    e.src.fail_opens = 10**6
    try:
        e.with_owner()
        e.svc.start()
        e.svc.set_mode(Mode.ACTIVE)
        end = time.time() + 3
        while time.time() < end and e.svc.component_status()["camera"]["state"] != "error":
            time.sleep(0.05)
        assert e.svc.component_status()["camera"]["state"] == "error"
        assert e.svc.mode == Mode.ACTIVE and e.events() == []  # sin imagen NO se inventan desconocidos
    finally:
        e.svc.shutdown()
