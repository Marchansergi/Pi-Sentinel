"""Convierte los eventos del seguimiento en historial, alarma y notificación.

- Aplica la política configurable por estado (ignore | log | alert) y el tiempo entre alertas.
- Alarma y ntfy se ejecutan de forma independiente y aislada: el fallo de una no impide la otra,
  y ninguna detiene la vigilancia.
- Solo se guardan metadatos: nunca imágenes, vídeo ni datos biométricos.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from .database import Database
from .domain import IdentityStatus
from .presence_tracker import EventType, TrackerEvent

log = logging.getLogger(__name__)

TITLES = {
    IdentityStatus.UNKNOWN: "⚠️ Pi Sentinel — Presencia desconocida",
    IdentityStatus.UNVERIFIED: "⚠️ Pi Sentinel — Presencia no verificada",
}
# Texto del campo «Estado:» (formato fijado en la especificación de la alerta)
STATE_TEXT = {
    IdentityStatus.AUTHORIZED: "autorizado",
    IdentityStatus.UNKNOWN: "desconocido",
    IdentityStatus.UNVERIFIED: "no verificable",
}
LEAD = {
    IdentityStatus.UNKNOWN: "Se ha detectado una persona no reconocida en la habitación.",
    IdentityStatus.UNVERIFIED: "Se ha detectado una persona que no se ha podido verificar en la habitación.",
}


def build_notification(status: IdentityStatus, event_id: str, device: str, ts: float) -> tuple[str, str]:
    """Texto de la alerta. No incluye nombres, imágenes ni datos biométricos."""
    dt = datetime.fromtimestamp(ts)
    body = "\n".join(
        [
            LEAD.get(status, LEAD[IdentityStatus.UNKNOWN]),
            f"Fecha: {dt.strftime('%d/%m/%Y')}",
            f"Hora: {dt.strftime('%H:%M:%S')}",
            f"Dispositivo: {device}",
            f"Evento: {event_id}",
            f"Estado: {STATE_TEXT[status]}",
            "Comprueba la situación si es necesario.",
        ]
    )
    return TITLES.get(status, TITLES[IdentityStatus.UNKNOWN]), body


class EventManager:
    def __init__(self, db: Database, get_settings: Callable[[], object], alarm, notifier, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._get = get_settings
        self._alarm = alarm
        self._notifier = notifier
        self._clock = clock
        self._lock = threading.Lock()
        self._last_alert_at: float | None = None
        self.last_event: dict | None = None

    # ------------------------------------------------------------------ flujo principal

    def handle(self, events: list[TrackerEvent], dry_run: bool = False) -> list[dict]:
        out: list[dict] = []
        for ev in events:
            try:
                r = self._handle_one(ev, dry_run)
                if r:
                    out.append(r)
            except Exception:  # un fallo con un evento no puede detener la vigilancia
                log.exception("Error gestionando el evento %s", ev.event_id)
        return out

    def _handle_one(self, ev: TrackerEvent, dry_run: bool) -> dict | None:
        if ev.type is EventType.ENDED:
            if not dry_run:
                self._db.execute("UPDATE events SET ended_at=? WHERE id=? AND ended_at IS NULL", (ev.at, ev.event_id))
            return None
        if ev.type is EventType.RECLASSIFIED:
            if not dry_run and ev.prev_event_id:
                self._db.execute(
                    "UPDATE events SET ended_at=?, detail=? WHERE id=?",
                    (ev.at, "Reclasificada posteriormente como persona autorizada", ev.prev_event_id),
                )
            return {"event_id": ev.event_id, "status": ev.status.value, "action": "reclassified", "dry_run": dry_run}

        settings = self._get()
        policy = getattr(settings.policy, ev.status.value.lower())
        result = {"event_id": ev.event_id, "status": ev.status.value, "policy": policy, "dry_run": dry_run, "at": ev.at}
        if policy == "ignore":
            result["action"] = "ignored"
            return result
        if dry_run:
            result["action"] = "would_alert" if policy == "alert" else "would_log"
            return result

        alarm_state, notify_state, detail = "NONE", "NONE", ""
        self._insert(ev, alarm_state, notify_state, detail)
        if policy == "alert":
            if self._in_cooldown(settings, ev.at):
                alarm_state = notify_state = "SUPPRESSED"
                detail = "Alerta suprimida por el tiempo de espera entre alertas"
            else:
                with self._lock:
                    self._last_alert_at = ev.at
                alarm_state = self._safe_alarm()
                notify_state = self._safe_notify(ev, settings)
            self._db.execute("UPDATE events SET alarm_state=?, notify_state=?, detail=? WHERE id=?", (alarm_state, notify_state, detail, ev.event_id))
        result.update(action="alerted" if policy == "alert" else "logged", alarm=alarm_state, notify=notify_state)
        self.last_event = {"id": ev.event_id, "status": ev.status.value, "at": ev.at}
        return result

    def _in_cooldown(self, settings, now: float) -> bool:
        with self._lock:
            last = self._last_alert_at
        return last is not None and (now - last) < settings.tracking.alert_cooldown_seconds

    def _insert(self, ev: TrackerEvent, alarm_state: str, notify_state: str, detail: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO events(id,started_at,ended_at,kind,identity_status,detail,alarm_state,notify_state,observations) "
            "VALUES(?,?,NULL,'presence',?,?,?,?,?)",
            (ev.event_id, ev.started_at, ev.status.value, detail, alarm_state, notify_state, ev.observations),
        )

    def _safe_alarm(self) -> str:
        try:
            return self._alarm.trigger()
        except Exception:
            log.exception("La alarma falló")
            return "ERROR"

    def _safe_notify(self, ev: TrackerEvent, settings) -> str:
        try:
            title, body = build_notification(ev.status, ev.event_id, settings.device_label(), ev.at)
            return self._notifier.enqueue_alert(ev.event_id, title, body)
        except Exception:
            log.exception("No se pudo encolar la notificación")
            return "ERROR"

    # ------------------------------------------------------------------ consulta / mantenimiento

    def list_events(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._db.query(
            "SELECT e.*, n.status AS n_status, n.attempts AS n_attempts, n.last_error AS n_error "
            "FROM events e LEFT JOIN notifications n ON n.event_id=e.id "
            "ORDER BY e.started_at DESC LIMIT ? OFFSET ?",
            (max(1, min(limit, 500)), max(0, offset)),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["notification"] = d.pop("n_status") or d["notify_state"]
            out.append(d)
        return out

    def recent_count(self, hours: float = 24.0) -> int:
        return self._db.query_one("SELECT COUNT(*) AS n FROM events WHERE started_at>=?", (self._clock() - hours * 3600,))["n"]

    def last(self) -> dict | None:
        r = self._db.query_one("SELECT id,started_at,identity_status,alarm_state,notify_state FROM events ORDER BY started_at DESC LIMIT 1")
        return dict(r) if r else None

    def purge(self, retention_days: int) -> int:
        return self._db.execute("DELETE FROM events WHERE started_at<?", (self._clock() - retention_days * 86400,)).rowcount

    def clear(self) -> int:
        n = self._db.execute("DELETE FROM events").rowcount
        self.last_event = None
        return n
