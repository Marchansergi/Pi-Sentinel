"""Controles de seguridad: secretos, contraseña, sesiones, CSRF, límites y redacción de logs."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- secretos


class SecretStore:
    """Secretos en un JSON con permisos 0600 (token de ntfy, hash de contraseña)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.error("secrets.json ilegible; se ignora")
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def get(self, key: str) -> str | None:
        with self._lock:
            v = self._read().get(key)
            return v if isinstance(v, str) and v else None

    def set(self, key: str, value: str | None) -> None:
        with self._lock:
            data = self._read()
            if value:
                data[key] = value
            else:
                data.pop(key, None)
            self._write(data)

    def ntfy_token(self) -> str | None:
        return os.environ.get("PI_SENTINEL_NTFY_TOKEN") or self.get("ntfy_token")

    def password_hash(self) -> str | None:
        return self.get("admin_password_hash")

    def all_secret_values(self) -> list[str]:
        vals = [self.ntfy_token()]
        return [v for v in vals if v]


def mask_token(token: str | None) -> str:
    """Nunca devuelve el token completo: solo un prefijo corto y los 4 últimos caracteres."""
    if not token:
        return ""
    if len(token) <= 10:
        return "••••"
    return f"{token[:3]}••••{token[-4:]}"


def generate_topic() -> str:
    """Tópico aleatorio de alta entropía (≈192 bits). No sustituye a la autenticación."""
    return "pisentinel-" + secrets.token_urlsafe(24)


# --------------------------------------------------------------------------- contraseña


_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


MIN_PASSWORD_LEN = 10


def validate_new_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres."
    if len(password) > 128:
        return "La contraseña es demasiado larga (máx. 128)."
    return None


# --------------------------------------------------------------------------- red


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class BindError(RuntimeError):
    pass


def validate_bind(host: str, allow_lan: bool, password_set: bool) -> None:
    """Rechaza exponer la interfaz si no se ha habilitado explícitamente y hay contraseña."""
    if is_loopback(host):
        return
    if not allow_lan:
        raise BindError(
            f"Se pidió escuchar en {host}, pero el acceso desde la red local no está habilitado. "
            "Habilítalo explícitamente (PI_SENTINEL_ALLOW_LAN=1 o web.allow_lan) y define una contraseña."
        )
    if not password_set:
        raise BindError(
            "El acceso desde la red local exige una contraseña de administración. "
            "Ejecuta: python -m app.main set-password"
        )


def allowed_hosts(host: str, port: int, extra: str = "") -> set[str]:
    """Cabeceras Host aceptadas (defensa frente a DNS rebinding)."""
    names = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}
    if host not in ("0.0.0.0", "::", ""):
        names.add(host)
    for h in extra.split(","):
        h = h.strip().lower()
        if h:
            names.add(h)
    return names


def host_ok(header: str | None, allowed: set[str], lan_open: bool) -> bool:
    if not header:
        return False
    h = header.strip().lower()
    if h.startswith("["):  # IPv6 literal
        name = h.split("]")[0] + "]"
    else:
        name = h.rsplit(":", 1)[0] if ":" in h else h
    if name in allowed:
        return True
    if lan_open:
        # Con la LAN habilitada se admiten IPs privadas literales (no nombres arbitrarios).
        try:
            ip = ipaddress.ip_address(name.strip("[]"))
            return ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            return False
    return False


# --------------------------------------------------------------------------- sesiones


class SessionManager:
    def __init__(self, ttl_seconds: float = 12 * 3600, clock=time.time) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def create(self, authed: bool) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self._lock:
            self._purge()
            self._sessions[token] = {"csrf": csrf, "authed": authed, "exp": self._clock() + self._ttl}
        return token, csrf

    def get(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if not s:
                return None
            if s["exp"] < self._clock():
                self._sessions.pop(token, None)
                return None
            return dict(s)

    def set_authed(self, token: str, authed: bool) -> None:
        with self._lock:
            if token in self._sessions:
                self._sessions[token]["authed"] = authed

    def destroy(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def destroy_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _purge(self) -> None:
        now = self._clock()
        for k in [k for k, v in self._sessions.items() if v["exp"] < now]:
            self._sessions.pop(k, None)


class RateLimiter:
    """Ventana deslizante por clave (IP)."""

    def __init__(self, max_events: int, window: float, clock=time.monotonic) -> None:
        self.max = max_events
        self.window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 2048:  # evita crecimiento sin límite
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    self._hits.pop(k, None)
            return True


class LoginGuard:
    """Bloqueo progresivo tras intentos fallidos de acceso."""

    def __init__(self, max_failures: int = 5, lock_seconds: float = 60.0, clock=time.monotonic) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}  # key -> (fallos, bloqueado_hasta)

    def locked_for(self, key: str) -> float:
        with self._lock:
            _, until = self._state.get(key, (0, 0.0))
            return max(0.0, until - self._clock())

    def failure(self, key: str) -> None:
        with self._lock:
            n, _ = self._state.get(key, (0, 0.0))
            n += 1
            until = 0.0
            if n >= self.max_failures:
                until = self._clock() + self.lock_seconds * (2 ** min(n - self.max_failures, 6))
            self._state[key] = (n, until)

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


# --------------------------------------------------------------------------- logs


_TOKEN_RE = re.compile(r"\btk_[A-Za-z0-9]{8,}\b")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{6,}")
_TOPIC_RE = re.compile(r"\bpisentinel-[A-Za-z0-9_-]{16,}\b")


class RedactingFilter(logging.Filter):
    """Oculta tokens, cabeceras Authorization y el tópico de ntfy en cualquier registro."""

    def __init__(self, extra_values=lambda: []) -> None:
        super().__init__()
        self._extra = extra_values

    def redact(self, text: str) -> str:
        text = _TOKEN_RE.sub("tk_***", text)
        text = _BEARER_RE.sub(r"\1***", text)
        text = _TOPIC_RE.sub("pisentinel-***", text)
        try:
            for v in self._extra():
                if v and len(v) >= 6:
                    text = text.replace(v, "***")
        except Exception:
            pass
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = self.redact(msg)
        if red != msg:
            record.msg = red
            record.args = None
        return True
