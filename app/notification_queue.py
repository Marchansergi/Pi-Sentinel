"""Cola persistente de notificaciones ntfy (SQLite) con reintentos y límites.

Estados: PENDING → SENT | FAILED. Una fila pasa a SENT solo tras una respuesta 2xx.
La detección y la alarma local nunca esperan a esta cola: `enqueue_alert` solo inserta una fila.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

from .database import Database
from .ntfy_client import NtfyClient, SendResult
from .security import mask_token

log = logging.getLogger(__name__)


class NotificationQueue:
    def __init__(
        self,
        db: Database,
        get_settings: Callable[[], object],
        secrets,
        client: NtfyClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._get_settings = get_settings
        self._secrets = secrets
        self._client = client or NtfyClient()
        self._clock = clock
        self._pass_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self._last_error: str = ""
        self._last_ok: bool | None = None
        row = self._db.query_one("SELECT MAX(sent_at) AS t FROM notifications WHERE status='SENT'")
        if row and row["t"]:
            self._last_success = row["t"]

    # ------------------------------------------------------------------ ciclo de vida

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ntfy-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_due()
            except Exception:  # el trabajador nunca debe morir
                log.exception("Error inesperado en el trabajador de notificaciones")
            self._wake.wait(self._next_wait())
            self._wake.clear()

    def _next_wait(self) -> float:
        row = self._db.query_one("SELECT MIN(next_attempt_at) AS t FROM notifications WHERE status='PENDING'")
        if not row or row["t"] is None:
            return 30.0
        return max(1.0, min(30.0, row["t"] - self._clock()))

    # ------------------------------------------------------------------ encolar

    def enqueue_alert(self, event_id: str, title: str, body: str, priority: int | None = None) -> str:
        """Devuelve QUEUED, DISABLED, NOT_CONFIGURED o DUPLICATE. Nunca bloquea ni lanza por red."""
        cfg = self._get_settings().ntfy
        if not cfg.enabled:
            return "DISABLED"
        if not cfg.topic:
            return "NOT_CONFIGURED"
        now = self._clock()
        cur = self._db.execute(
            "INSERT OR IGNORE INTO notifications(event_id,kind,title,body,priority,status,attempts,next_attempt_at,created_at) "
            "VALUES(?,?,?,?,?,'PENDING',0,?,?)",
            (event_id, "alert", title, body, priority or cfg.priority, now, now),
        )
        if cur.rowcount == 0:
            return "DUPLICATE"
        self._enforce_limits(cfg)
        self._wake.set()
        return "QUEUED"

    def _enforce_limits(self, cfg) -> None:
        now = self._clock()
        # 1) antigüedad
        cutoff = now - cfg.max_age_hours * 3600
        old = self._db.query("SELECT id,event_id FROM notifications WHERE status='PENDING' AND created_at<?", (cutoff,))
        for r in old:
            self._fail(r["id"], f"Descartada por antigüedad (> {cfg.max_age_hours:g} h sin poder enviarse)")
            log.warning("Notificación %s descartada por antigüedad", r["event_id"])
        # 2) capacidad
        n = self._db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE status='PENDING'")["n"]
        if n > cfg.queue_max:
            drop = self._db.query(
                "SELECT id,event_id FROM notifications WHERE status='PENDING' ORDER BY created_at ASC LIMIT ?", (n - cfg.queue_max,)
            )
            for r in drop:
                self._fail(r["id"], "Descartada: cola de pendientes llena")
                log.warning("Notificación %s descartada: cola llena", r["event_id"])

    def _fail(self, row_id: int, reason: str) -> None:
        self._db.execute("UPDATE notifications SET status='FAILED', last_error=? WHERE id=?", (reason[:300], row_id))

    # ------------------------------------------------------------------ envío

    def backoff(self, attempts: int, cfg=None) -> float:
        cfg = cfg or self._get_settings().ntfy
        delay = min(cfg.retry_max_seconds, cfg.retry_base_seconds * (2 ** max(0, attempts - 1)))
        return delay * random.uniform(0.9, 1.1) if delay < cfg.retry_max_seconds else delay

    def process_due(self, limit: int = 10) -> int:
        """Procesa las notificaciones vencidas. Devuelve cuántas se intentaron."""
        with self._pass_lock:
            cfg = self._get_settings().ntfy
            self._enforce_limits(cfg)
            if not cfg.enabled or not cfg.topic:
                return 0
            token = self._secrets.ntfy_token()
            now = self._clock()
            rows = self._db.query(
                "SELECT * FROM notifications WHERE status='PENDING' AND next_attempt_at<=? ORDER BY created_at LIMIT ?",
                (now, limit),
            )
            tried = 0
            for r in rows:
                if self._stop.is_set():
                    break
                res = self._client.publish(
                    cfg.server, cfg.topic, r["title"], r["body"], r["priority"], token, cfg.timeout_seconds, ["warning"]
                )
                tried += 1
                self._record_attempt(r, res, cfg)
                if not res.ok and res.retryable:
                    break  # sin red / servidor caído: no tiene sentido insistir con el resto ahora
            return tried

    def _record_attempt(self, row, res: SendResult, cfg) -> None:
        now = self._clock()
        attempts = row["attempts"] + 1
        self._last_attempt = now
        if res.ok:
            self._db.execute(
                "UPDATE notifications SET status='SENT', attempts=?, sent_at=?, last_error='', last_status_code=? WHERE id=?",
                (attempts, now, res.status_code, row["id"]),
            )
            self._last_success, self._last_ok, self._last_error = now, True, ""
            log.info("Notificación %s enviada (HTTP %s)", row["event_id"], res.status_code)
            return
        self._last_ok, self._last_error = False, res.error
        if not res.retryable:
            self._db.execute(
                "UPDATE notifications SET status='FAILED', attempts=?, last_error=?, last_status_code=? WHERE id=?",
                (attempts, res.error[:300], res.status_code, row["id"]),
            )
            log.error("Notificación %s fallida sin reintento: %s", row["event_id"], res.error)
            return
        if attempts >= cfg.max_attempts:
            self._db.execute(
                "UPDATE notifications SET status='FAILED', attempts=?, last_error=?, last_status_code=? WHERE id=?",
                (attempts, f"Máximo de reintentos alcanzado. Último error: {res.error}"[:300], res.status_code, row["id"]),
            )
            log.error("Notificación %s descartada tras %d intentos: %s", row["event_id"], attempts, res.error)
            return
        delay = self.backoff(attempts, cfg)
        if res.retry_after:
            delay = max(delay, min(res.retry_after, cfg.retry_max_seconds))
        nxt = now + delay
        self._db.execute(
            "UPDATE notifications SET attempts=?, next_attempt_at=?, last_error=?, last_status_code=? WHERE id=?",
            (attempts, nxt, res.error[:300], res.status_code, row["id"]),
        )
        # circuito: el resto de pendientes tampoco se reintenta antes
        self._db.execute("UPDATE notifications SET next_attempt_at=MAX(next_attempt_at,?) WHERE status='PENDING'", (nxt,))
        log.warning("Notificación %s: %s. Reintento en %.0fs (intento %d/%d)", row["event_id"], res.error, delay, attempts, cfg.max_attempts)

    def send_test(self) -> dict:
        """Envío inmediato y explícito (botón «probar»). No se reintenta: se informa al usuario."""
        cfg = self._get_settings().ntfy
        if not cfg.topic:
            return {"ok": False, "error": "Configura primero un tópico."}
        now = self._clock()
        label = self._get_settings().device_label()
        title, body = "✅ Pi Sentinel — Notificación de prueba", f"Esta es una notificación de prueba enviada desde {label}."
        res = self._client.publish(cfg.server, cfg.topic, title, body, cfg.priority, self._secrets.ntfy_token(), cfg.timeout_seconds, ["white_check_mark"])
        self._last_attempt = now
        if res.ok:
            self._last_success, self._last_ok, self._last_error = now, True, ""
        else:
            self._last_ok, self._last_error = False, res.error
        self._db.execute(
            "INSERT INTO notifications(event_id,kind,title,body,priority,status,attempts,next_attempt_at,last_error,last_status_code,created_at,sent_at) "
            "VALUES(NULL,'test',?,?,?,?,1,0,?,?,?,?)",
            (title, body, cfg.priority, "SENT" if res.ok else "FAILED", res.error[:300], res.status_code, now, now if res.ok else None),
        )
        return {"ok": res.ok, "status_code": res.status_code, "error": res.error, "kind": res.kind}

    def retry_failed(self) -> int:
        """Devuelve a PENDING las alertas FAILED que aún no han caducado."""
        cfg = self._get_settings().ntfy
        cutoff = self._clock() - cfg.max_age_hours * 3600
        cur = self._db.execute(
            "UPDATE notifications SET status='PENDING', attempts=0, next_attempt_at=0, last_error='' "
            "WHERE status='FAILED' AND kind='alert' AND created_at>=?",
            (cutoff,),
        )
        if cur.rowcount:
            self._wake.set()
        return cur.rowcount

    # ------------------------------------------------------------------ consulta

    def status(self) -> dict:
        cfg = self._get_settings().ntfy
        counts = {r["status"]: r["n"] for r in self._db.query("SELECT status, COUNT(*) AS n FROM notifications GROUP BY status")}
        if not cfg.enabled:
            state, msg = "disabled", "Notificaciones desactivadas"
        elif not cfg.topic:
            state, msg = "unconfigured", "Falta configurar el tópico"
        elif self._last_ok is False:
            state, msg = "error", self._last_error or "Último envío fallido"
        elif self._last_ok is True:
            state, msg = "ok", "Última entrega correcta"
        else:
            state, msg = "idle", "Configurado; aún sin envíos en esta sesión"
        token = self._secrets.ntfy_token()
        return {
            "state": state,
            "message": msg,
            "pending": counts.get("PENDING", 0),
            "sent": counts.get("SENT", 0),
            "failed": counts.get("FAILED", 0),
            "last_attempt": self._last_attempt,
            "last_success": self._last_success,
            "last_error": self._last_error,
            "token_set": bool(token),
            "token_hint": mask_token(token),
        }

    def history(self, limit: int = 50) -> list[dict]:
        rows = self._db.query(
            "SELECT id,event_id,kind,title,priority,status,attempts,next_attempt_at,last_error,last_status_code,created_at,sent_at "
            "FROM notifications ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        )
        return [dict(r) for r in rows]

    def get_for_event(self, event_id: str) -> dict | None:
        r = self._db.query_one("SELECT status,attempts,last_error FROM notifications WHERE event_id=?", (event_id,))
        return dict(r) if r else None

    def purge(self, retention_days: int, hard_cap: int = 2000) -> int:
        cutoff = self._clock() - retention_days * 86400
        n = self._db.execute("DELETE FROM notifications WHERE status IN ('SENT','FAILED') AND created_at<?", (cutoff,)).rowcount
        extra = self._db.execute(
            "DELETE FROM notifications WHERE status IN ('SENT','FAILED') AND id NOT IN "
            "(SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
            (hard_cap,),
        ).rowcount
        return n + extra

    def clear_history(self) -> int:
        """Borra el historial ya resuelto (no toca los pendientes de enviar)."""
        return self._db.execute("DELETE FROM notifications WHERE status IN ('SENT','FAILED')").rowcount
