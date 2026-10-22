import logging
import time

import pytest

from app.config import ConfigStore
from app.database import Database
from app.notification_queue import NotificationQueue
from app.ntfy_client import NtfyClient
from app.security import RedactingFilter, SecretStore
from conftest import free_port

TOKEN = "tk_abcdefghijklmnop12345678"
TOPIC = "pisentinel-" + "x" * 32


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t


class Stack:
    def __init__(self, tmp_path, server_url, db=None, clock=None, enabled=True, token=TOKEN, **ntfy):
        self.db = db or Database(tmp_path / "n.db")
        self.cfg = ConfigStore(self.db)
        patch = {"server": server_url, "topic": TOPIC, "enabled": enabled, "timeout_seconds": 2.0, "retry_base_seconds": 5.0, "retry_max_seconds": 60.0}
        patch.update(ntfy)
        self.cfg.update({"ntfy": patch})
        self.secrets = SecretStore(tmp_path / "secrets.json")
        if token:
            self.secrets.set("ntfy_token", token)
        self.clock = clock or Clock()
        self.q = NotificationQueue(self.db, self.cfg.get, self.secrets, NtfyClient(), self.clock)

    def row(self, event_id):
        return self.db.query_one("SELECT * FROM notifications WHERE event_id=?", (event_id,))


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    monkeypatch.delenv("PI_SENTINEL_NTFY_TOKEN", raising=False)


def test_successful_send_format_and_auth(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url)
    assert s.q.enqueue_alert("ev1", "⚠️ Título — ñ", "cuerpo\nlinea2") == "QUEUED"
    assert s.row("ev1")["status"] == "PENDING"
    assert s.q.process_due() == 1
    r = s.row("ev1")
    assert r["status"] == "SENT" and r["attempts"] == 1 and r["sent_at"] and r["last_status_code"] == 200
    req = ntfy_server.requests[0]
    assert req["body"]["topic"] == TOPIC and req["body"]["title"] == "⚠️ Título — ñ"  # UTF-8 íntegro
    assert req["body"]["priority"] == 4
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert set(req["body"]) <= {"topic", "title", "message", "priority", "tags"}  # sin campos extra / biométricos


def test_no_token_means_no_authorization_header(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url, token=None)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    assert "Authorization" not in ntfy_server.requests[0]["headers"]


def test_incorrect_token_fails_without_retry_and_is_clear(tmp_path, ntfy_server):
    ntfy_server.script = [401]
    s = Stack(tmp_path, ntfy_server.url)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    r = s.row("ev")
    assert r["status"] == "FAILED" and r["attempts"] == 1 and "token" in r["last_error"].lower()
    assert s.q.process_due() == 0 and len(ntfy_server.requests) == 1  # no insiste
    assert s.q.status()["state"] == "error"


def test_http_500_keeps_pending_with_backoff_then_recovers(tmp_path, ntfy_server):
    ntfy_server.script = [500, 503]
    s = Stack(tmp_path, ntfy_server.url)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    r = s.row("ev")
    assert r["status"] == "PENDING" and r["attempts"] == 1 and r["next_attempt_at"] > s.clock.t
    assert s.q.process_due() == 0  # todavía no toca
    s.clock.t += 6
    s.q.process_due()  # 503
    assert s.row("ev")["status"] == "PENDING" and s.row("ev")["attempts"] == 2
    assert s.row("ev")["next_attempt_at"] - s.clock.t >= 8.9  # la espera crece (≈10 s)
    s.clock.t += 12
    s.q.process_due()  # 200
    assert s.row("ev")["status"] == "SENT" and s.row("ev")["attempts"] == 3
    assert len(ntfy_server.requests) == 3


def test_connection_error_is_retryable(tmp_path):
    s = Stack(tmp_path, f"http://127.0.0.1:{free_port()}")
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    r = s.row("ev")
    assert r["status"] == "PENDING" and "conexión" in r["last_error"].lower()
    assert s.q.status()["state"] == "error" and s.q.status()["pending"] == 1


def test_timeout_is_retryable(tmp_path, ntfy_server):
    ntfy_server.script = [("sleep", 2.0)]
    s = Stack(tmp_path, ntfy_server.url, timeout_seconds=1.0)
    s.q.enqueue_alert("ev", "t", "b")
    t0 = time.time()
    s.q.process_due()
    assert time.time() - t0 < 1.9
    r = s.row("ev")
    assert r["status"] == "PENDING" and "tiempo" in r["last_error"].lower()


def test_rate_limit_honours_retry_after(tmp_path, ntfy_server):
    ntfy_server.script = [(429, {"Retry-After": "40"})]
    s = Stack(tmp_path, ntfy_server.url)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    assert s.row("ev")["next_attempt_at"] - s.clock.t >= 39


def test_max_attempts_then_failed(tmp_path, ntfy_server):
    ntfy_server.default_status = 500
    s = Stack(tmp_path, ntfy_server.url, max_attempts=3)
    s.q.enqueue_alert("ev", "t", "b")
    for _ in range(5):
        s.clock.t += 100
        s.q.process_due()
    r = s.row("ev")
    assert r["status"] == "FAILED" and r["attempts"] == 3 and "reintentos" in r["last_error"].lower()
    assert len(ntfy_server.requests) == 3


def test_message_never_marked_sent_without_success(tmp_path, ntfy_server):
    ntfy_server.default_status = 500
    s = Stack(tmp_path, ntfy_server.url)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    assert s.row("ev")["sent_at"] is None and s.row("ev")["status"] != "SENT"


def test_outage_recovery_flushes_queue_in_order(tmp_path, ntfy_server):
    url = ntfy_server.url
    s = Stack(tmp_path, f"http://127.0.0.1:{free_port()}")  # sin conexión
    for i in range(3):
        s.q.enqueue_alert(f"ev{i}", f"t{i}", "b")
    s.q.process_due()
    assert s.q.status()["pending"] == 3 and s.q.status()["sent"] == 0
    s.cfg.update({"ntfy": {"server": url}})  # vuelve la conexión
    s.clock.t += 120
    s.q.process_due()
    assert s.q.status()["pending"] == 0 and s.q.status()["sent"] == 3
    assert [r["body"]["title"] for r in ntfy_server.requests] == ["t0", "t1", "t2"]


def test_queue_survives_restart(tmp_path, ntfy_server):
    s = Stack(tmp_path, f"http://127.0.0.1:{free_port()}")
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    s.db.close()
    db2 = Database(tmp_path / "n.db")  # «reinicio» de la aplicación
    s2 = Stack(tmp_path, ntfy_server.url, db=db2, clock=s.clock)
    assert s2.q.status()["pending"] == 1
    s2.clock.t += 120
    s2.q.process_due()
    assert s2.row("ev")["status"] == "SENT"


def test_duplicates_are_prevented(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url)
    assert s.q.enqueue_alert("ev", "t", "b") == "QUEUED"
    assert s.q.enqueue_alert("ev", "t", "b") == "DUPLICATE"
    s.q.process_due()
    assert s.q.enqueue_alert("ev", "t", "b") == "DUPLICATE"  # tampoco tras enviarse
    s.q.process_due()
    assert len(ntfy_server.requests) == 1


def test_disabled_and_unconfigured_do_not_queue(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url, enabled=False)
    assert s.q.enqueue_alert("a", "t", "b") == "DISABLED"
    s.cfg.update({"ntfy": {"enabled": True, "topic": ""}})
    assert s.q.enqueue_alert("b", "t", "b") == "NOT_CONFIGURED"
    assert s.q.status()["pending"] == 0 and ntfy_server.requests == []


def test_old_notifications_are_discarded_and_logged(tmp_path, ntfy_server, caplog):
    s = Stack(tmp_path, f"http://127.0.0.1:{free_port()}", max_age_hours=1.0)
    s.q.enqueue_alert("old", "t", "b")
    s.clock.t += 2 * 3600
    with caplog.at_level(logging.WARNING):
        s.q.process_due()
    r = s.row("old")
    assert r["status"] == "FAILED" and "antigüedad" in r["last_error"]
    assert "descartada por antigüedad" in caplog.text


def test_queue_capacity_is_bounded(tmp_path):
    s = Stack(tmp_path, f"http://127.0.0.1:{free_port()}", queue_max=2)
    for i in range(5):
        s.clock.t += 1
        s.q.enqueue_alert(f"e{i}", "t", "b")
    assert s.q.status()["pending"] == 2
    assert s.row("e0")["status"] == "FAILED" and "llena" in s.row("e0")["last_error"]
    assert s.row("e4")["status"] == "PENDING"


def test_test_notification_is_explicit_and_recorded(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url, enabled=False)  # funciona aunque estén desactivadas
    res = s.q.send_test()
    assert res["ok"] and "prueba" in ntfy_server.requests[0]["body"]["title"].lower()
    h = s.q.history()[0]
    assert h["kind"] == "test" and h["status"] == "SENT"
    assert s.q.status()["pending"] == 0
    ntfy_server.script = [403]
    res = s.q.send_test()
    assert not res["ok"] and "token" in res["error"].lower()


def test_test_notification_without_topic(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url)
    s.cfg.update({"ntfy": {"topic": ""}})
    assert not s.q.send_test()["ok"]


def test_nothing_is_sent_automatically_without_user_action_or_event(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url)
    s.q.process_due()
    assert ntfy_server.requests == []


def test_secrets_never_logged_or_stored_in_db(tmp_path, ntfy_server, caplog):
    ntfy_server.default_status = 500
    s = Stack(tmp_path, ntfy_server.url)
    with caplog.at_level(logging.DEBUG):
        s.q.enqueue_alert("ev", "t", "b")
        s.q.process_due()
        s.q.send_test()
    assert TOKEN not in caplog.text and TOPIC not in caplog.text
    dump = " ".join(str(dict(r)) for r in s.db.query("SELECT * FROM notifications"))
    assert TOKEN not in dump and TOPIC not in dump
    assert TOKEN not in str(s.q.status()) and "tk_" not in s.q.status()["token_hint"][:2] or "••••" in s.q.status()["token_hint"]


def test_log_filter_redacts_secrets():
    f = RedactingFilter(lambda: ["miSecretoLargo123"])
    for raw in (f"token {TOKEN}", "Authorization: Bearer abcdef123456", f"topic {TOPIC}", "x miSecretoLargo123 y"):
        rec = logging.LogRecord("t", logging.INFO, "f", 1, raw, None, None)
        f.filter(rec)
        assert TOKEN not in rec.msg and TOPIC not in rec.msg and "abcdef123456" not in rec.msg and "miSecretoLargo123" not in rec.msg


def test_worker_thread_delivers_in_background(tmp_path, ntfy_server):
    s = Stack(tmp_path, ntfy_server.url)
    s.clock = time.time
    s.q._clock = time.time
    s.q.start()
    try:
        s.q.enqueue_alert("bg", "t", "b")
        end = time.time() + 5
        while time.time() < end and s.row("bg")["status"] != "SENT":
            time.sleep(0.05)
        assert s.row("bg")["status"] == "SENT"
    finally:
        s.q.stop()


def test_enqueue_does_not_block_on_network(tmp_path):
    s = Stack(tmp_path, f"http://10.255.255.1:81")  # no enrutable: un envío síncrono tardaría
    t0 = time.time()
    s.q.enqueue_alert("ev", "t", "b")
    assert time.time() - t0 < 0.5


def test_retry_failed_requeues_recent_alerts(tmp_path, ntfy_server):
    ntfy_server.script = [401]
    s = Stack(tmp_path, ntfy_server.url)
    s.q.enqueue_alert("ev", "t", "b")
    s.q.process_due()
    assert s.row("ev")["status"] == "FAILED"
    assert s.q.retry_failed() == 1
    s.q.process_due()  # el servidor ya responde 200
    assert s.row("ev")["status"] == "SENT"


def test_server_url_validation():
    from app.config import NtfySettings

    for bad in ("ftp://x", "https://user:pw@ntfy.sh", "ntfy.sh", "https://ntfy.sh?x=1"):
        with pytest.raises(ValueError):
            NtfySettings(server=bad)
    assert NtfySettings(server="https://ntfy.sh/").server == "https://ntfy.sh"
    with pytest.raises(ValueError):
        NtfySettings(topic="con espacios/y barra")
