"""Registro de auditoría: acciones administrativas (quién, cuándo, qué). Nunca guarda secretos."""
from __future__ import annotations

import logging
import time
from typing import Callable

from .database import Database

log = logging.getLogger(__name__)

MAX_ROWS = 5000

ACTION_LABELS = {
    "login.ok": "Inicio de sesión",
    "login.fail": "Intento de acceso fallido",
    "logout": "Cierre de sesión",
    "password.change": "Contraseña cambiada",
    "mode.change": "Modo de vigilancia",
    "settings.update": "Ajustes modificados",
    "person.create": "Persona creada",
    "person.update": "Persona modificada",
    "person.delete": "Persona eliminada",
    "sample.add": "Muestra facial añadida",
    "sample.delete": "Muestra facial eliminada",
    "biometrics.delete_all": "Datos biométricos eliminados",
    "sound.upload": "Sonido subido",
    "sound.delete": "Sonido eliminado",
    "ntfy.token": "Token de ntfy modificado",
    "ntfy.topic": "Tópico de ntfy regenerado",
    "history.clear": "Historial borrado",
    "notifications.clear": "Historial de envíos borrado",
}


class AuditLog:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    def record(self, action: str, detail: str = "", ip: str = "") -> None:
        try:
            self._db.execute("INSERT INTO audit_log(ts,ip,action,detail) VALUES(?,?,?,?)",
                             (self._clock(), ip[:64], action[:64], detail[:300]))
            self._db.execute("DELETE FROM audit_log WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)", (MAX_ROWS,))
        except Exception:  # la auditoría nunca debe romper la operación auditada
            log.exception("No se pudo registrar la auditoría")

    def list(self, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        rows = self._db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
                              (max(1, min(limit, 500)), max(0, offset)))
        total = self._db.query_one("SELECT COUNT(*) AS n FROM audit_log")["n"]
        out = []
        for r in rows:
            d = dict(r)
            d["label"] = ACTION_LABELS.get(d["action"], d["action"])
            out.append(d)
        return out, total

    def purge(self, retention_days: int) -> int:
        return self._db.execute("DELETE FROM audit_log WHERE ts<?", (self._clock() - retention_days * 86400,)).rowcount
