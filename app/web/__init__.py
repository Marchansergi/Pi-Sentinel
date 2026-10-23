"""Interfaz web local (FastAPI).

Seguridad:
- Escucha en 127.0.0.1 por defecto (ver main.py/security.validate_bind).
- Sesión con cookie HttpOnly + SameSite=Strict, token CSRF obligatorio en métodos que modifican.
- Validación de la cabecera Host (anti DNS-rebinding), límites de tamaño y de frecuencia.
- Con contraseña definida (obligatoria si se abre a la red) toda la API exige iniciar sesión.
- El token de ntfy nunca se devuelve completo; el tópico solo se muestra al pedirlo explícitamente.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import hmac
import io
from datetime import datetime
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..authorized_people import ProfileError
from ..security import (
    LoginGuard,
    RateLimiter,
    SessionManager,
    allowed_hosts,
    generate_topic,
    hash_password,
    host_ok,
    is_loopback,
    mask_token,
    validate_new_password,
    verify_password,
)
from ..service import Mode, SentinelService, ServiceError
from ..sound_alert import AudioError, audio_diagnostics, play_test_tone
from ..system_info import APP_VERSION, system_info

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "ps_session"
CSRF_HEADER = "x-csrf-token"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY = 64 * 1024
MAX_UPLOAD = 8 * 1024 * 1024


class ModeReq(BaseModel):
    mode: Literal["stopped", "test", "active"]


class LoginReq(BaseModel):
    password: str = Field(max_length=128)


class PasswordReq(BaseModel):
    current: str = Field(default="", max_length=128)
    new: str = Field(max_length=128)


class PersonReq(BaseModel):
    name: str = Field(max_length=60)


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    enabled: bool | None = None


class CaptureReq(BaseModel):
    pose: Literal["any", "frontal", "lateral"] = "any"


class CalibrateReq(BaseModel):
    kind: Literal["genuine", "impostor"]
    frames: int = Field(default=12, ge=3, le=40)


class ConfirmReq(BaseModel):
    confirm: str = Field(max_length=32)


class MuteReq(BaseModel):
    minutes: float | None = Field(default=30, ge=0, le=1440)


class TokenReq(BaseModel):
    token: str | None = Field(default=None, max_length=256)


class AudioTestReq(BaseModel):
    player: Literal["auto", "pw-play", "paplay", "aplay"] = "auto"
    device: str = Field(default="", max_length=96, pattern=r"^[A-Za-z0-9_:,.=@-]*$")


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in d.items():
        key = f"{prefix}{k}"
        out += _flatten_keys(v, key + ".") if isinstance(v, dict) else [key]
    return out


def _fmt_validation(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e["loc"])
        parts.append(f"{loc}: {e['msg']}")
    return "; ".join(parts)


def create_app(
    service: SentinelService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_lan: bool = False,
    extra_hosts: str = "",
    rate_limit: int = 40,
    on_shutdown=None,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        if on_shutdown:  # se ejecuta en el apagado ordenado de uvicorn (también con SIGTERM)
            await asyncio.get_running_loop().run_in_executor(None, on_shutdown)

    app = FastAPI(title="Pi Sentinel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    sessions = SessionManager()
    limiter = RateLimiter(rate_limit, 1.0)
    guard = LoginGuard()
    hosts = allowed_hosts(host, port, extra_hosts)
    lan_open = allow_lan and not is_loopback(host)
    secrets = service.secrets

    def auth_required() -> bool:
        return bool(secrets.password_hash()) or lan_open

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def audit(request: Request, action: str, detail: str = "") -> None:
        service.audit.record(action, detail, client_ip(request))

    # ------------------------------------------------------------------ middleware

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if not host_ok(request.headers.get("host"), hosts, lan_open):
            return JSONResponse({"detail": "Host no permitido"}, status_code=400)
        ip = request.client.host if request.client else "?"
        if not limiter.allow(ip):
            return JSONResponse({"detail": "Demasiadas peticiones"}, status_code=429, headers={"Retry-After": "1"})
        if request.method in UNSAFE:
            limit = MAX_UPLOAD if request.url.path.startswith("/api/alarm/sounds") else MAX_BODY
            cl = request.headers.get("content-length")
            if cl is None:
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "Se requiere Content-Length"}, status_code=411)
            else:
                try:
                    if int(cl) > limit:
                        return JSONResponse({"detail": "Petición demasiado grande"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length no válido"}, status_code=400)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ errores

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.exception_handler(ProfileError)
    async def _profile_error(request: Request, exc: ProfileError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(AudioError)
    async def _audio_error(request: Request, exc: AudioError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def _val_error(request: Request, exc: ValidationError):
        return JSONResponse({"detail": _fmt_validation(exc)}, status_code=422)

    # ------------------------------------------------------------------ auth

    def current(request: Request) -> tuple[str | None, dict | None]:
        tok = request.cookies.get(COOKIE)
        return tok, sessions.get(tok)

    def require(request: Request) -> dict:
        tok, sess = current(request)
        if sess is None:
            raise HTTPException(401, "Sesión no iniciada")
        if auth_required() and not sess["authed"]:
            raise HTTPException(401, "Autenticación requerida")
        if request.method in UNSAFE:
            sent = request.headers.get(CSRF_HEADER, "")
            if not hmac.compare_digest(sent, sess["csrf"]):
                raise HTTPException(403, "Token CSRF no válido")
        return sess

    def set_cookie(request: Request, response: Response, token: str) -> None:
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/", max_age=12 * 3600)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/session")
    def session_info(request: Request, response: Response):
        tok, sess = current(request)
        if sess is None:
            tok, csrf = sessions.create(authed=not auth_required())
            set_cookie(request, response, tok)
            sess = {"csrf": csrf, "authed": not auth_required()}
        return {
            "csrf": sess["csrf"],
            "auth_required": auth_required(),
            "authenticated": bool(sess["authed"]) or not auth_required(),
            "password_set": bool(secrets.password_hash()),
            "lan": lan_open,
            "first_run": len(service.people.list_people()) == 0,
            "version": APP_VERSION,
        }

    @app.post("/api/login")
    def login(req: LoginReq, request: Request, response: Response):
        tok, sess = current(request)
        if sess is None or not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), sess["csrf"]):
            raise HTTPException(403, "Token CSRF no válido")
        ip = request.client.host if request.client else "?"
        wait = guard.locked_for(ip)
        if wait > 0:
            raise HTTPException(429, f"Demasiados intentos. Espera {wait:.0f}s.")
        if not verify_password(req.password, secrets.password_hash()):
            guard.failure(ip)
            audit(request, "login.fail")
            raise HTTPException(401, "Contraseña incorrecta")
        guard.success(ip)
        audit(request, "login.ok")
        sessions.destroy(tok)  # nueva sesión tras autenticarse (evita fijación)
        ntok, csrf = sessions.create(authed=True)
        set_cookie(request, response, ntok)
        return {"ok": True, "csrf": csrf}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        tok, _ = current(request)
        require(request)
        audit(request, "logout")
        sessions.destroy(tok)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/password")
    def change_password(req: PasswordReq, request: Request, response: Response):
        require(request)
        stored = secrets.password_hash()
        if stored and not verify_password(req.current, stored):
            raise HTTPException(403, "La contraseña actual no es correcta")
        problem = validate_new_password(req.new)
        if problem:
            raise HTTPException(422, problem)
        secrets.set("admin_password_hash", hash_password(req.new))
        audit(request, "password.change")
        sessions.destroy_all()
        tok, csrf = sessions.create(authed=True)
        set_cookie(request, response, tok)
        return {"ok": True, "csrf": csrf}

    # ------------------------------------------------------------------ estado / modo

    @app.get("/api/status")
    def status(request: Request):
        require(request)
        return service.status()

    @app.post("/api/surveillance")
    def surveillance(req: ModeReq, request: Request):
        require(request)
        service.set_mode(req.mode)
        audit(request, "mode.change", req.mode)
        return {"mode": service.mode}

    # ------------------------------------------------------------------ ajustes

    def public_settings() -> dict:
        d = service.config.get().model_dump()
        topic = d["ntfy"].pop("topic")
        d["ntfy"]["topic_set"] = bool(topic)
        d["ntfy"]["topic_masked"] = (topic[:4] + "••••" + topic[-3:]) if len(topic) > 8 else ("••••" if topic else "")
        return d

    @app.get("/api/settings")
    def get_settings(request: Request):
        require(request)
        return public_settings()

    @app.put("/api/settings")
    def put_settings(patch: dict[str, Any], request: Request):
        require(request)
        web = patch.get("web") or {}
        if web.get("allow_lan") and not secrets.password_hash():
            raise HTTPException(409, "Define primero una contraseña de administración antes de permitir el acceso desde la red.")
        _, restart = service.config.update(patch)
        audit(request, "settings.update", ", ".join(_flatten_keys(patch))[:300])
        return {"settings": public_settings(), "restart_required": restart}

    # ------------------------------------------------------------------ cámara

    @app.get("/api/camera/devices")
    def camera_devices(request: Request):
        require(request)
        return {"devices": service.camera_list(), "status": service.camera.status()}

    @app.get("/api/camera/snapshot.jpg")
    def snapshot(request: Request, overlay: int = 1):
        require(request)
        data = service.preview_jpeg(overlay=bool(overlay))
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/camera/check")
    def camera_check(request: Request):
        require(request)
        return service.camera_check()

    # ------------------------------------------------------------------ personas

    def person_json(p) -> dict:
        return {"id": p.id, "name": p.name, "enabled": p.enabled, "sample_count": p.sample_count, "ready": p.ready,
                "created_at": p.created_at, "updated_at": p.updated_at}

    @app.get("/api/people")
    def people_list(request: Request):
        require(request)
        cfg = service.config.get().face
        return {"people": [person_json(p) for p in service.people.list_people()],
                "min_samples": cfg.min_samples, "threshold": cfg.similarity_threshold,
                "calibration": service.calibration_summary()}

    @app.post("/api/people")
    def people_create(req: PersonReq, request: Request):
        require(request)
        p = service.people.create(req.name)
        audit(request, "person.create", f"#{p.id}")
        return person_json(p)

    @app.patch("/api/people/{pid}")
    def people_patch(pid: int, req: PersonPatch, request: Request):
        require(request)
        p = service.people.get(pid)
        if req.name is not None:
            p = service.people.rename(pid, req.name)
        if req.enabled is not None:
            p = service.people.set_enabled(pid, req.enabled)
        audit(request, "person.update", f"#{pid}" + ("" if req.enabled is None else (" activado" if req.enabled else " desactivado")))
        return person_json(p)

    @app.delete("/api/people/{pid}")
    def people_delete(pid: int, request: Request):
        require(request)
        service.people.delete_person(pid)
        audit(request, "person.delete", f"#{pid}")
        return {"ok": True}

    @app.get("/api/people/{pid}/samples")
    def samples_list(pid: int, request: Request):
        require(request)
        return {"samples": [{"id": s.id, "quality": s.quality, "pose": s.pose, "created_at": s.created_at} for s in service.people.samples(pid)]}

    @app.post("/api/people/{pid}/samples/capture")
    def samples_capture(pid: int, req: CaptureReq, request: Request):
        require(request)
        r = service.capture_sample(pid, req.pose)
        if r.get("ok"):
            audit(request, "sample.add", f"persona #{pid}")
        return r

    @app.delete("/api/people/{pid}/samples/{sid}")
    def samples_delete(pid: int, sid: int, request: Request):
        require(request)
        service.people.delete_sample(pid, sid)
        audit(request, "sample.delete", f"persona #{pid}")
        return {"ok": True}

    @app.post("/api/recognition/test")
    def recognition_test(request: Request):
        require(request)
        return service.diagnose_recognition()

    @app.post("/api/recognition/calibrate")
    def recognition_calibrate(req: CalibrateReq, request: Request):
        require(request)
        return service.calibrate(req.kind, req.frames)

    @app.post("/api/biometrics/delete-all")
    def biometrics_delete_all(req: ConfirmReq, request: Request):
        require(request)
        if req.confirm != "ELIMINAR":
            raise HTTPException(422, "Escribe ELIMINAR para confirmar")
        service.set_mode(Mode.STOPPED)
        n = service.people.delete_all()
        service.db.kv_delete("calibration_genuine")
        service.db.kv_delete("calibration_impostor")
        audit(request, "biometrics.delete_all", f"{n} perfiles")
        return {"deleted_profiles": n}

    # ------------------------------------------------------------------ alarma

    @app.get("/api/alarm")
    def alarm_status(request: Request):
        require(request)
        return {"status": service.alarm.status(), "chime": service.chime.status(), "sounds": service.alarm.list_sounds()}

    @app.post("/api/alarm/test")
    def alarm_test(request: Request):
        require(request)
        state = service.alarm.trigger(force=True, wait=2.5)
        return {"state": state, "error": service.alarm.last_error if state == "ERROR" else ""}

    @app.post("/api/alarm/chime-test")
    def chime_test(request: Request):
        require(request)
        state = service.chime.trigger(force=True, wait=2.5)
        return {"state": state, "error": service.chime.last_error if state == "ERROR" else ""}

    @app.post("/api/alarm/stop")
    def alarm_stop(request: Request):
        require(request)
        service.alarm.stop()
        service.chime.stop()
        return {"ok": True}

    @app.post("/api/alarm/mute")
    def alarm_mute(req: MuteReq, request: Request):
        require(request)
        until = service.alarm.mute(req.minutes)
        service.chime.mute(req.minutes)  # «silenciar» afecta a la alarma y al sonido de entrada
        return {"muted": service.alarm.is_muted(), "until": until or None}

    @app.post("/api/alarm/sounds")
    async def alarm_upload(request: Request, name: str):
        require(request)
        if len(name) > 120:
            raise HTTPException(422, "Nombre demasiado largo")
        data = await request.body()
        # la conversión (ffmpeg) puede tardar: fuera del bucle de eventos
        saved, truncated = await asyncio.get_running_loop().run_in_executor(None, service.alarm.add_user_sound, name, data)
        audit(request, "sound.upload", saved)
        return {"ok": True, "name": saved, "truncated": truncated, "sounds": service.alarm.list_sounds()}

    @app.get("/api/alarm/sounds/{name}")
    def alarm_get_sound(name: str, request: Request):
        require(request)
        path = service.alarm.resolve(name)  # valida el nombre (sin rutas)
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.delete("/api/alarm/sounds/{name}")
    def alarm_delete_sound(name: str, request: Request):
        require(request)
        service.alarm.delete_user_sound(name)
        cfg = service.config.get()
        if cfg.alarm.sound == name:
            service.config.update({"alarm": {"sound": "default"}})
        if cfg.entry.sound == name:
            service.config.update({"entry": {"sound": "entrada.wav"}})
        audit(request, "sound.delete", name)
        return {"ok": True, "sounds": service.alarm.list_sounds()}

    # ------------------------------------------------------------------ diagnóstico de audio

    @app.get("/api/audio/diagnostics")
    def audio_diag(request: Request):
        require(request)
        a = service.config.get().alarm
        return audio_diagnostics(a.player, a.audio_device)

    @app.post("/api/audio/test")
    def audio_test(req: AudioTestReq, request: Request):
        require(request)
        a = service.config.get().alarm
        return play_test_tone(service.alarm.backend, service.paths.tmp_dir, req.player, req.device, a.lead_in_seconds)

    # ------------------------------------------------------------------ notificaciones

    @app.get("/api/notifications")
    def notif_get(request: Request):
        require(request)
        cfg = service.config.get().ntfy
        return {"status": service.notifier.status(), "history": service.notifier.history(50),
                "config": {"enabled": cfg.enabled, "server": cfg.server, "priority": cfg.priority, "topic_set": bool(cfg.topic)}}

    @app.get("/api/notifications/topic")
    def notif_topic(request: Request):
        require(request)
        cfg = service.config.get().ntfy
        return {"topic": cfg.topic, "subscribe_url": f"{cfg.server}/{cfg.topic}" if cfg.topic else ""}

    @app.post("/api/notifications/generate-topic")
    def notif_generate_topic(request: Request):
        require(request)
        service.config.update({"ntfy": {"topic": generate_topic()}})
        audit(request, "ntfy.topic")
        return notif_topic(request)

    @app.put("/api/notifications/token")
    def notif_token(req: TokenReq, request: Request):
        require(request)
        tok = (req.token or "").strip()
        if tok and (len(tok) < 8 or any(c.isspace() for c in tok)):
            raise HTTPException(422, "El token no es válido")
        secrets.set("ntfy_token", tok or None)
        audit(request, "ntfy.token", "definido" if tok else "eliminado")
        return {"token_set": bool(secrets.ntfy_token()), "token_hint": mask_token(secrets.ntfy_token())}

    @app.post("/api/notifications/test")
    def notif_test(request: Request):
        require(request)
        return service.notifier.send_test()

    @app.post("/api/notifications/retry-failed")
    def notif_retry(request: Request):
        require(request)
        return {"requeued": service.notifier.retry_failed()}

    @app.delete("/api/notifications/history")
    def notif_clear(request: Request):
        require(request)
        audit(request, "notifications.clear")
        return {"deleted": service.notifier.clear_history()}

    # ------------------------------------------------------------------ historial

    def _status_filter(status: str | None) -> str | None:
        if status and status not in ("AUTHORIZED", "UNKNOWN", "UNVERIFIED"):
            raise HTTPException(422, "Filtro de estado no válido")
        return status or None

    @app.get("/api/events")
    def events_list(request: Request, limit: int = 50, offset: int = 0, status: str | None = None,
                    since: float | None = None, until: float | None = None):
        require(request)
        st = _status_filter(status)
        return {"events": service.events.list_events(limit, offset, st, since, until),
                "total": service.events.count_events(st, since, until)}

    @app.get("/api/events.csv")
    def events_csv(request: Request, status: str | None = None, since: float | None = None, until: float | None = None):
        require(request)
        rows = service.events.list_events(500, 0, _status_filter(status), since, until)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["inicio", "fin", "evento", "estado", "alarma", "notificacion", "observaciones", "detalle"])
        fmt = lambda t: datetime.fromtimestamp(t).isoformat(sep=" ", timespec="seconds") if t else ""
        for e in rows:
            w.writerow([fmt(e["started_at"]), fmt(e["ended_at"]), e["id"], e["identity_status"], e["alarm_state"],
                        e["notification"], e["observations"], e["detail"]])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="pi-sentinel-eventos.csv"'})

    @app.delete("/api/events")
    def events_clear(request: Request):
        require(request)
        n = service.events.clear()
        audit(request, "history.clear", f"{n} eventos")
        return {"deleted": n}

    # ------------------------------------------------------------------ sistema / auditoría

    @app.get("/api/system")
    def system(request: Request):
        require(request)
        return system_info(service.paths.data_dir, service.paths.db_path)

    @app.get("/api/audit")
    def audit_list(request: Request, limit: int = 100, offset: int = 0):
        require(request)
        rows, total = service.audit.list(limit, offset)
        return {"entries": rows, "total": total}

    # ------------------------------------------------------------------ estáticos

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
