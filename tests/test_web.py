import json
import wave

import pytest
from fastapi.testclient import TestClient

from app.config import Paths
from app.domain import IdentityStatus as S
from app.face_recognition import FaceQuality
from app.security import BindError, host_ok, allowed_hosts, validate_bind
from app.service import FACE_DET_MODEL, FACE_REC_MODEL, PERSON_MODEL, SentinelService
from app.sound_alert import generate_default_wav
from app.web import create_app
from conftest import FakeAudio, SourceFactory, StubDetector, StubFaces, face_obs, noisy, person_box, unit

TOKEN = "tk_supersecretvalue1234567890"


@pytest.fixture
def paths(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    for n in (PERSON_MODEL, FACE_DET_MODEL, FACE_REC_MODEL):
        (models / n).write_bytes(b"x")
    return Paths(tmp_path / "data", models, tmp_path / "sounds")


def build(paths, **app_kw):
    svc = SentinelService(paths, source_factory=SourceFactory(), detector=StubDetector(), faces=StubFaces(), audio_backend=FakeAudio())
    svc.alarm.ensure_default()
    return svc, create_app(svc, **app_kw)


class Api:
    """Cliente que gestiona sesión y CSRF como lo haría la interfaz."""

    def __init__(self, app, host="testserver"):
        self.c = TestClient(app, base_url=f"http://{host}")
        self.csrf = None

    def start(self):
        r = self.c.get("/api/session")
        self.csrf = r.json()["csrf"]
        return r.json()

    def call(self, method, url, **kw):
        headers = kw.pop("headers", {})
        if self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.c.request(method, url, headers=headers, **kw)

    def get(self, url, **kw): return self.call("GET", url, **kw)
    def post(self, url, **kw): return self.call("POST", url, **kw)
    def put(self, url, **kw): return self.call("PUT", url, **kw)
    def patch(self, url, **kw): return self.call("PATCH", url, **kw)
    def delete(self, url, **kw): return self.call("DELETE", url, **kw)


@pytest.fixture
def web(paths):
    svc, app = build(paths)
    api = Api(app)
    api.start()
    yield svc, api
    svc.shutdown()


# ------------------------------------------------------------------ acceso / CSRF


def test_requires_session_before_any_api_call(paths):
    svc, app = build(paths)
    try:
        assert TestClient(app).get("/api/status").status_code == 401
    finally:
        svc.shutdown()


def test_loopback_without_password_works_after_session(web):
    svc, api = web
    info = api.start()
    assert info["auth_required"] is False and info["first_run"] is True
    assert api.get("/api/status").status_code == 200


def test_csrf_required_on_state_changing_requests(web):
    svc, api = web
    assert api.c.post("/api/people", json={"name": "Ana"}).status_code == 403  # sin cabecera
    assert api.c.post("/api/people", json={"name": "Ana"}, headers={"X-CSRF-Token": "otro"}).status_code == 403
    assert api.post("/api/people", json={"name": "Ana"}).status_code == 200
    assert api.get("/api/people").status_code == 200  # GET no lo exige


def test_foreign_host_header_is_rejected(web):
    svc, api = web
    assert api.c.get("/api/session", headers={"host": "evil.example.com"}).status_code == 400
    assert api.c.get("/healthz", headers={"host": "attacker.test:8765"}).status_code == 400


def test_security_headers_and_cookie_flags(paths):
    svc, app = build(paths)
    try:
        c = TestClient(app)
        r = c.get("/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert "default-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        sc = c.get("/api/session").headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=strict" in sc
        assert c.get("/api/session").headers["cache-control"] == "no-store"
    finally:
        svc.shutdown()


def test_oversized_body_rejected(web):
    svc, api = web
    r = api.post("/api/people", content=b"x" * 70_000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_rate_limit(paths):
    svc, app = build(paths, rate_limit=5)
    try:
        c = TestClient(app)
        codes = [c.get("/healthz").status_code for _ in range(12)]
        assert 429 in codes and codes[0] == 200
    finally:
        svc.shutdown()


def test_password_protects_everything_and_login_lockout(paths):
    svc, app = build(paths)
    try:
        admin = Api(app)
        admin.start()
        assert admin.post("/api/password", json={"new": "corta"}).status_code == 422
        r = admin.post("/api/password", json={"new": "una-contraseña-larga"})
        assert r.status_code == 200
        assert "una-contraseña" not in r.text and "scrypt" not in r.text
        assert "scrypt" in svc.secrets.password_hash()  # solo se guarda el hash

        anon = Api(app)
        info = anon.start()
        assert info["auth_required"] and not info["authenticated"]
        assert anon.get("/api/status").status_code == 401
        assert anon.get("/api/people").status_code == 401
        assert anon.get("/api/camera/snapshot.jpg").status_code == 401  # nada de imagen ni biometría sin login
        ok = Api(app)
        ok.start()
        r = ok.post("/api/login", json={"password": "una-contraseña-larga"})
        assert r.status_code == 200
        ok.csrf = r.json()["csrf"]
        assert ok.get("/api/status").status_code == 200
        assert ok.post("/api/logout").status_code == 200
        assert ok.get("/api/status").status_code == 401

        for _ in range(5):
            assert anon.post("/api/login", json={"password": "mala"}).status_code == 401
        # bloqueado: ni siquiera la contraseña correcta entra mientras dura el bloqueo
        assert anon.post("/api/login", json={"password": "una-contraseña-larga"}).status_code == 429
    finally:
        svc.shutdown()


def test_changing_password_requires_current_one(paths):
    svc, app = build(paths)
    try:
        a = Api(app)
        a.start()
        a.csrf = a.post("/api/password", json={"new": "primera-contraseña"}).json()["csrf"]
        assert a.post("/api/password", json={"current": "x", "new": "segunda-contraseña"}).status_code == 403
        assert a.post("/api/password", json={"current": "primera-contraseña", "new": "segunda-contraseña"}).status_code == 200
    finally:
        svc.shutdown()


def test_old_sessions_invalidated_when_password_set(paths):
    svc, app = build(paths)
    try:
        victim = Api(app)
        victim.start()
        admin = Api(app)
        admin.start()
        admin.post("/api/password", json={"new": "una-contraseña-larga"})
        assert victim.get("/api/status").status_code == 401
    finally:
        svc.shutdown()


def test_lan_mode_requires_login_even_without_password_flag(paths):
    svc, app = build(paths, host="0.0.0.0", allow_lan=True)
    try:
        c = Api(app, host="192.168.1.50:8765")
        info = c.start()
        assert info["auth_required"] and info["lan"] and not info["authenticated"]
        assert c.get("/api/status").status_code == 401
        assert Api(app, host="evil.example.com").c.get("/api/session").status_code == 400  # ni nombres arbitrarios
    finally:
        svc.shutdown()


def test_bind_validation():
    validate_bind("127.0.0.1", False, False)
    validate_bind("::1", False, False)
    with pytest.raises(BindError):
        validate_bind("0.0.0.0", False, True)  # sin habilitar explícitamente
    with pytest.raises(BindError):
        validate_bind("192.168.1.5", True, False)  # sin contraseña
    validate_bind("0.0.0.0", True, True)


def test_host_header_logic():
    hosts = allowed_hosts("127.0.0.1", 8765, "pi.local")
    assert host_ok("localhost:8765", hosts, False) and host_ok("pi.local", hosts, False)
    assert not host_ok("192.168.1.9:8765", hosts, False)
    assert host_ok("192.168.1.9:8765", hosts, True)
    assert not host_ok("8.8.8.8", hosts, True) and not host_ok(None, hosts, True)


# ------------------------------------------------------------------ configuración


def test_settings_validation(web):
    svc, api = web
    assert api.put("/api/settings", json={"detection": {"analysis_fps": 100}}).status_code == 422
    assert api.put("/api/settings", json={"ntfy": {"server": "ftp://x"}}).status_code == 422
    assert api.put("/api/settings", json={"alarm": {"sound": "../../etc/passwd"}}).status_code == 422
    assert api.put("/api/settings", json={"camera": {"device": "rm -rf /"}}).status_code == 422
    assert api.put("/api/settings", json={"nuevo_campo": 1}).status_code == 422
    assert api.put("/api/settings", json={"policy": {"unknown": "explotar"}}).status_code == 422
    assert svc.config.get().detection.analysis_fps == 2.0  # nada cambió


def test_settings_persist_across_restart(paths):
    svc, app = build(paths)
    api = Api(app)
    api.start()
    r = api.put("/api/settings", json={"alarm": {"volume": 0.3, "repetitions": 7}, "policy": {"unverified": "alert"}})
    assert r.status_code == 200 and r.json()["restart_required"] == []
    svc.shutdown()
    svc2, _ = build(paths)
    try:
        cfg = svc2.config.get()
        assert cfg.alarm.volume == 0.3 and cfg.alarm.repetitions == 7 and cfg.policy.unverified == "alert"
    finally:
        svc2.shutdown()


def test_web_settings_flag_restart_and_lan_needs_password(web):
    svc, api = web
    assert api.put("/api/settings", json={"web": {"allow_lan": True}}).status_code == 409
    r = api.put("/api/settings", json={"web": {"port": 9000}})
    assert r.status_code == 200 and r.json()["restart_required"] == ["web"]


def test_corrupt_saved_settings_fall_back_to_defaults(paths):
    svc, _ = build(paths)
    svc.db.kv_set("settings", "{esto no es json")
    svc.shutdown()
    svc2, _ = build(paths)
    try:
        assert svc2.config.get().detection.analysis_fps == 2.0
    finally:
        svc2.shutdown()


def test_secrets_are_never_exposed(web, ntfy_server):
    svc, api = web
    assert api.put("/api/notifications/token", json={"token": TOKEN}).status_code == 200
    topic = api.post("/api/notifications/generate-topic").json()["topic"]
    assert topic.startswith("pisentinel-") and len(topic) >= 40
    api.put("/api/settings", json={"ntfy": {"enabled": True, "server": ntfy_server.url}})
    api.post("/api/notifications/test")
    dumps = [api.get(u).text for u in ("/api/settings", "/api/notifications", "/api/status", "/api/events")]
    for d in dumps:
        assert TOKEN not in d and topic not in d  # ni el token ni el tópico salen en las vistas habituales
    n = api.get("/api/notifications").json()
    assert n["status"]["token_set"] and "••••" in n["status"]["token_hint"] and TOKEN not in n["status"]["token_hint"]
    assert api.get("/api/notifications/topic").json()["topic"] == topic  # solo a petición explícita
    assert api.put("/api/notifications/token", json={"token": "x y"}).status_code == 422
    assert api.put("/api/notifications/token", json={"token": None}).json()["token_set"] is False


def test_test_notification_sends_real_http_request(web, ntfy_server):
    svc, api = web
    api.put("/api/settings", json={"ntfy": {"server": ntfy_server.url, "topic": "pisentinel-" + "b" * 30}})
    r = api.post("/api/notifications/test")
    assert r.json()["ok"] is True and len(ntfy_server.requests) == 1
    ntfy_server.script = [500]
    r = api.post("/api/notifications/test")
    assert r.json()["ok"] is False and "500" in r.json()["error"]
    assert any(h["kind"] == "test" for h in api.get("/api/notifications").json()["history"])


# ------------------------------------------------------------------ perfiles / vigilancia


def test_people_lifecycle_and_biometric_deletion(web):
    svc, api = web
    pid = api.post("/api/people", json={"name": "Ana"}).json()["id"]
    assert api.post("/api/people", json={"name": "Ana"}).status_code == 422
    assert api.post("/api/people", json={"name": "<img src=x onerror=alert(1)>"}).status_code == 422
    assert api.patch(f"/api/people/{pid}", json={"enabled": False}).json()["enabled"] is False
    assert api.patch(f"/api/people/{pid}", json={"name": "Ana M"}).json()["name"] == "Ana M"
    for i in range(5):
        svc.faces.sample = (noisy(unit(1), 0.05, i), FaceQuality(True, (), 100, 120, 0.0, 120), None)
        r = api.post(f"/api/people/{pid}/samples/capture", json={"pose": "any"})
        assert r.json()["ok"]
    assert len(api.get(f"/api/people/{pid}/samples").json()["samples"]) == 5
    sid = api.get(f"/api/people/{pid}/samples").json()["samples"][0]["id"]
    assert api.delete(f"/api/people/{pid}/samples/{sid}").status_code == 200
    assert api.delete(f"/api/people/{pid}/samples/{sid}").status_code == 422
    assert api.delete(f"/api/people/{pid}").status_code == 200
    assert api.get("/api/people").json()["people"] == []
    assert svc.db.query_one("SELECT COUNT(*) AS n FROM face_samples")["n"] == 0


def test_delete_all_biometrics_needs_confirmation(web):
    svc, api = web
    api.post("/api/people", json={"name": "Ana"})
    api.post("/api/people", json={"name": "Beto"})
    assert api.post("/api/biometrics/delete-all", json={"confirm": "no"}).status_code == 422
    assert len(api.get("/api/people").json()["people"]) == 2
    assert api.post("/api/biometrics/delete-all", json={"confirm": "ELIMINAR"}).json()["deleted_profiles"] == 2
    assert api.get("/api/people").json()["people"] == []


def test_start_and_stop_surveillance(web):
    svc, api = web
    r = api.post("/api/surveillance", json={"mode": "active"})
    assert r.status_code == 409 and "perfil" in r.json()["detail"]
    assert api.post("/api/surveillance", json={"mode": "bogus"}).status_code == 422
    pid = api.post("/api/people", json={"name": "Ana"}).json()["id"]
    for i in range(5):
        svc.people.add_sample(pid, noisy(unit(1), 0.05, i), 0.9)
    assert api.post("/api/surveillance", json={"mode": "active"}).json()["mode"] == "active"
    assert api.get("/api/status").json()["mode"] == "active"
    assert api.post("/api/surveillance", json={"mode": "stopped"}).json()["mode"] == "stopped"
    assert api.get("/api/status").json()["mode"] == "stopped"


def test_status_contains_all_dashboard_fields(web):
    svc, api = web
    s = api.get("/api/status").json()
    for k in ("mode", "components", "ntfy", "alarm", "last_event", "recent_events_24h", "people", "device_name"):
        assert k in s
    for c in ("camera", "detector", "face", "ntfy", "alarm", "analysis"):
        assert s["components"][c]["state"] in ("ok", "warn", "error", "disabled")


def test_snapshot_returns_jpeg_without_writing_files(web):
    svc, api = web
    svc.start()
    r = api.get("/api/camera/snapshot.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8" and r.headers["cache-control"] == "no-store"
    assert [p for p in svc.paths.data_dir.rglob("*") if p.suffix.lower() in (".jpg", ".png")] == []


def test_recognition_test_endpoint(web):
    svc, api = web
    svc.start()
    r = api.post("/api/recognition/test")
    assert r.status_code == 200 and r.json()["faces"] == [] and r.json()["profiles_active"] == 0


# ------------------------------------------------------------------ alarma / historial


def test_alarm_test_reports_audio_problem_clearly(paths):
    svc = SentinelService(paths, source_factory=SourceFactory(), detector=StubDetector(), faces=StubFaces(), audio_backend=FakeAudio(fail_check=True))
    svc.alarm.ensure_default()
    api = Api(create_app(svc))
    api.start()
    try:
        r = api.post("/api/alarm/test").json()
        assert r["state"] == "ERROR" and "reproductor" in r["error"]
        assert api.get("/api/alarm").json()["status"]["audio_available"] is False
    finally:
        svc.shutdown()


def test_alarm_mute_and_test(web):
    svc, api = web
    assert api.post("/api/alarm/test").json()["state"] == "TRIGGERED"
    svc.alarm.join()
    assert api.post("/api/alarm/mute", json={"minutes": 15}).json()["muted"] is True
    assert api.post("/api/alarm/mute", json={"minutes": None}).json()["muted"] is False
    assert api.post("/api/alarm/mute", json={"minutes": 99999}).status_code == 422


def test_sound_upload_validation(web, tmp_path):
    svc, api = web
    good = tmp_path / "ok.wav"
    generate_default_wav(good)
    hdr = {"Content-Type": "audio/wav"}
    assert api.post("/api/alarm/sounds?name=sirena.wav", content=good.read_bytes(), headers=hdr).status_code == 200
    assert api.post("/api/alarm/sounds?name=../evil.wav", content=good.read_bytes(), headers=hdr).status_code == 422
    assert api.post("/api/alarm/sounds?name=falso.wav", content=b"no soy un wav", headers=hdr).status_code == 422
    assert api.post("/api/alarm/sounds?name=default", content=good.read_bytes(), headers=hdr).status_code == 422
    api.put("/api/settings", json={"alarm": {"sound": "sirena.wav"}})
    assert api.delete("/api/alarm/sounds/sirena.wav").status_code == 200
    assert svc.config.get().alarm.sound == "default"  # no queda apuntando a un sonido borrado


def test_history_list_and_clear(web):
    svc, api = web
    pid = svc.people.create("Ana").id
    for i in range(5):
        svc.people.add_sample(pid, noisy(unit(1), 0.05, i), 0.9)
    svc.config.update({"tracking": {"confirm_seconds": 0, "min_observations": 2}, "detection": {"motion_gate": False}})
    svc.set_mode("active")
    svc.detector.persons = [person_box()]
    svc.faces.faces = [face_obs(S.UNKNOWN, sim=0.1)]
    for _ in range(3):
        svc._process(__import__("conftest").blank_frame(), svc.config.get())
    ev = api.get("/api/events").json()["events"]
    assert len(ev) == 1 and ev[0]["identity_status"] == "UNKNOWN"
    assert "id" in ev[0] and "started_at" in ev[0] and "alarm_state" in ev[0] and "notification" in ev[0]
    assert api.delete("/api/events").json()["deleted"] == 1
    assert api.get("/api/events").json()["events"] == []
    assert api.get("/api/status").json()["recent_events_24h"] == 0


def test_graceful_shutdown_hook_runs_on_app_stop(paths):
    called = []
    svc = SentinelService(paths, source_factory=SourceFactory(), detector=StubDetector(), faces=StubFaces(), audio_backend=FakeAudio())
    app = create_app(svc, on_shutdown=lambda: called.append(1))
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
    assert called == [1]
    svc.shutdown()
    svc.shutdown()  # idempotente
