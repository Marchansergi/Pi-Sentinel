"""Configuración de Pi Sentinel.

- `Settings` es el esquema validado (pydantic). Se persiste como JSON en la tabla `kv` de SQLite.
- Los secretos (token de ntfy, hash de la contraseña) NO están aquí: viven en `security.SecretStore`.
- `ConfigStore` entrega copias inmutables de la configuración para evitar compartir objetos
  mutables entre hilos.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

Policy = Literal["ignore", "log", "alert"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CameraSettings(_Model):
    backend: Literal["auto", "opencv", "picamera2"] = "auto"
    # Índice (0, 1…) o ruta (/dev/video0). Con picamera2 se interpreta como índice de cámara.
    device: str = Field(default="0", max_length=64)
    width: int = Field(default=640, ge=160, le=1920)
    height: int = Field(default=480, ge=120, le=1080)
    capture_fps: float = Field(default=10.0, ge=1.0, le=30.0)

    @field_validator("device")
    @classmethod
    def _device(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(r"\d{1,2}|/dev/video\d{1,2}", v):
            raise ValueError("el dispositivo debe ser un índice (0, 1…) o una ruta /dev/videoN")
        return v


class DetectionSettings(_Model):
    analysis_fps: float = Field(default=2.0, ge=0.1, le=10.0)
    person_confidence: float = Field(default=0.5, ge=0.1, le=0.99)
    nms_threshold: float = Field(default=0.45, ge=0.1, le=0.9)
    # Lado de entrada de YOLOX (múltiplo de 32). Menor = más rápido y menos preciso con personas lejanas.
    input_size: int = Field(default=416, ge=320, le=640)
    min_area_ratio: float = Field(default=0.004, ge=0.0, le=0.5)
    motion_gate: bool = True
    # Con la escena estática se analiza como mucho cada tantos segundos.
    max_idle_seconds: float = Field(default=2.0, ge=0.5, le=30.0)

    @field_validator("input_size")
    @classmethod
    def _mult32(cls, v: int) -> int:
        if v % 32:
            raise ValueError("input_size debe ser múltiplo de 32")
        return v


class FaceSettings(_Model):
    # Umbral de similitud coseno de SFace. 0.363 es el valor recomendado por OpenCV/SFace en LFW;
    # NO es una probabilidad. Calíbralo con tus propias muestras (ver README).
    similarity_threshold: float = Field(default=0.363, ge=0.2, le=0.9)
    min_detection_score: float = Field(default=0.8, ge=0.5, le=0.99)
    min_face_px: int = Field(default=56, ge=24, le=400)
    min_sharpness: float = Field(default=20.0, ge=0.0, le=500.0)
    max_yaw: float = Field(default=0.55, ge=0.1, le=1.5)
    min_samples: int = Field(default=5, ge=1, le=50)


class TrackingSettings(_Model):
    confirm_seconds: float = Field(default=3.0, ge=0.0, le=60.0)
    min_observations: int = Field(default=3, ge=1, le=50)
    min_authorized_observations: int = Field(default=2, ge=1, le=50)
    exit_seconds: float = Field(default=10.0, ge=1.0, le=600.0)
    alert_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    unverified_timeout_seconds: float = Field(default=12.0, ge=1.0, le=600.0)


class PolicySettings(_Model):
    authorized: Policy = "ignore"
    unknown: Policy = "alert"
    unverified: Policy = "log"


class AlarmSettings(_Model):
    enabled: bool = True
    sound: str = "default"
    volume: float = Field(default=0.8, ge=0.0, le=1.0)
    duration_seconds: float = Field(default=10.0, ge=1.0, le=300.0)
    repetitions: int = Field(default=3, ge=1, le=50)
    min_interval_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    audio_device: str = Field(default="", max_length=64)

    @field_validator("sound")
    @classmethod
    def _sound(cls, v: str) -> str:
        if v != "default" and not re.fullmatch(r"[A-Za-z0-9_. -]{1,60}\.wav", v):
            raise ValueError("nombre de sonido no válido (solo .wav, sin rutas)")
        if ".." in v:
            raise ValueError("nombre de sonido no válido")
        return v

    @field_validator("audio_device")
    @classmethod
    def _dev(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9_:,.=-]{1,64}", v):
            raise ValueError("dispositivo de audio no válido")
        return v


class EntrySettings(AlarmSettings):
    """Sonido breve cuando se confirma que alguien ha entrado (independiente de la alarma)."""

    enabled: bool = True
    sound: str = "entrada.wav"
    volume: float = Field(default=0.7, ge=0.0, le=1.0)
    duration_seconds: float = Field(default=4.0, ge=1.0, le=300.0)
    repetitions: int = Field(default=1, ge=1, le=50)
    min_interval_seconds: float = Field(default=10.0, ge=0.0, le=3600.0)
    on_authorized: bool = True
    on_unknown: bool = True
    on_unverified: bool = True


class NtfySettings(_Model):
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = Field(default="", max_length=64)
    priority: int = Field(default=4, ge=1, le=5)
    timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    retry_base_seconds: float = Field(default=5.0, ge=1.0, le=600.0)
    retry_max_seconds: float = Field(default=900.0, ge=5.0, le=86400.0)
    max_attempts: int = Field(default=12, ge=1, le=100)
    queue_max: int = Field(default=200, ge=1, le=5000)
    max_age_hours: float = Field(default=24.0, ge=0.1, le=720.0)

    @field_validator("server")
    @classmethod
    def _server(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("la URL del servidor debe empezar por http:// o https://")
        if p.username or p.password or p.query or p.fragment:
            raise ValueError("la URL del servidor no debe incluir credenciales, parámetros ni fragmentos")
        return v

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v):
            raise ValueError("el tópico solo admite letras, números, '_' y '-' (máx. 64)")
        return v


class WebSettings(_Model):
    """Estos ajustes requieren reiniciar el servicio."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    allow_lan: bool = False


class StorageSettings(_Model):
    history_retention_days: int = Field(default=30, ge=1, le=3650)


class GeneralSettings(_Model):
    device_name: str = Field(default="", max_length=64)

    @field_validator("device_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if any(ord(c) < 32 for c in v):
            raise ValueError("el nombre del dispositivo contiene caracteres de control")
        return v


class Settings(_Model):
    general: GeneralSettings = GeneralSettings()
    camera: CameraSettings = CameraSettings()
    detection: DetectionSettings = DetectionSettings()
    face: FaceSettings = FaceSettings()
    tracking: TrackingSettings = TrackingSettings()
    policy: PolicySettings = PolicySettings()
    alarm: AlarmSettings = AlarmSettings()
    entry: EntrySettings = EntrySettings()
    ntfy: NtfySettings = NtfySettings()
    web: WebSettings = WebSettings()
    storage: StorageSettings = StorageSettings()

    def device_label(self) -> str:
        return self.general.device_name or socket.gethostname()


RESTART_REQUIRED = {"web"}


# --------------------------------------------------------------------------- rutas / entorno


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    models_dir: Path
    builtin_sounds_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sentinel.db"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.json"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def user_sounds_dir(self) -> Path:
        return self.data_dir / "sounds"


def load_dotenv(path: Path) -> None:
    """Cargador mínimo de .env: no pisa variables ya definidas en el entorno."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def resolve_paths(env: dict[str, str] | None = None) -> Paths:
    env = os.environ if env is None else env
    data = Path(env.get("PI_SENTINEL_DATA_DIR") or PROJECT_ROOT / "data").expanduser()
    models = Path(env.get("PI_SENTINEL_MODELS_DIR") or PROJECT_ROOT / "models").expanduser()
    return Paths(data_dir=data, models_dir=models, builtin_sounds_dir=PROJECT_ROOT / "sounds")


# --------------------------------------------------------------------------- almacén


def _prune(model_cls: type[BaseModel], data: Any) -> Any:
    """Elimina claves desconocidas (p. ej. de versiones antiguas) antes de validar."""
    if not isinstance(data, dict):
        return data
    out = {}
    for name, field in model_cls.model_fields.items():
        if name in data:
            ann = field.annotation
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                out[name] = _prune(ann, data[name])
            else:
                out[name] = data[name]
    return out


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigStore:
    """Configuración persistente y segura entre hilos. `get()` devuelve una copia profunda."""

    KEY = "settings"

    def __init__(self, db) -> None:
        self._db = db
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Settings, Settings], None]] = []
        self._settings = self._load()

    def _load(self) -> Settings:
        raw = self._db.kv_get(self.KEY)
        if not raw:
            return Settings()
        try:
            return Settings.model_validate(_prune(Settings, json.loads(raw)))
        except (ValueError, ValidationError) as exc:
            log.error("Configuración guardada no válida (%s); se usan valores por defecto", exc)
            self._db.kv_set(self.KEY + "_invalid_backup", raw)
            return Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, patch: dict[str, Any]) -> tuple[Settings, list[str]]:
        """Valida y guarda un parche parcial. Lanza ValidationError si no es válido.

        Devuelve (nueva configuración, secciones que requieren reinicio).
        """
        with self._lock:
            old = self._settings
            merged = _deep_merge(old.model_dump(), patch)
            new = Settings.model_validate(merged)
            self._db.kv_set(self.KEY, new.model_dump_json())
            self._settings = new
            listeners = list(self._listeners)
        changed_restart = [s for s in RESTART_REQUIRED if getattr(old, s) != getattr(new, s)]
        for fn in listeners:
            try:
                fn(old.model_copy(deep=True), new.model_copy(deep=True))
            except Exception:  # un oyente defectuoso no debe invalidar el guardado
                log.exception("Error en un oyente de cambio de configuración")
        return new.model_copy(deep=True), changed_restart

    def on_change(self, fn: Callable[[Settings, Settings], None]) -> None:
        with self._lock:
            self._listeners.append(fn)
