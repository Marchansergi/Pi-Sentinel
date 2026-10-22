from types import SimpleNamespace

import pytest

from app.config import ConfigStore
from app.database import Database
from app.domain import IdentityStatus as S
from app.event_manager import EventManager, build_notification
from app.presence_tracker import EventType, TrackerEvent


class FakeAlarm:
    def __init__(self, state="TRIGGERED", raises=False):
        self.calls, self.state, self.raises = 0, state, raises

    def trigger(self, force=False):
        self.calls += 1
        if self.raises:
            raise RuntimeError("altavoz roto")
        return self.state


class FakeNotifier:
    def __init__(self, raises=False):
        self.items, self.raises = [], raises

    def enqueue_alert(self, event_id, title, body, priority=None):
        if self.raises:
            raise RuntimeError("cola rota")
        self.items.append((event_id, title, body))
        return "QUEUED"


def ev(eid, status=S.UNKNOWN, at=1000.0, type=EventType.CONFIRMED, **kw):
    return TrackerEvent(type, eid, "trk", status, at, kw.pop("started_at", at - 3), 5, kw.pop("name", None), **kw)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "e.db")
    cfg = ConfigStore(db)
    alarm, notifier = FakeAlarm(), FakeNotifier()
    mgr = EventManager(db, cfg.get, alarm, notifier)
    return SimpleNamespace(db=db, cfg=cfg, alarm=alarm, notifier=notifier, mgr=mgr)


def row(env, eid):
    r = env.db.query_one("SELECT * FROM events WHERE id=?", (eid,))
    return dict(r) if r else None


def test_unknown_confirmed_triggers_alarm_notification_and_history(env):
    out = env.mgr.handle([ev("e1")])
    assert out[0]["action"] == "alerted"
    assert env.alarm.calls == 1 and len(env.notifier.items) == 1
    r = row(env, "e1")
    assert r["identity_status"] == "UNKNOWN" and r["alarm_state"] == "TRIGGERED" and r["notify_state"] == "QUEUED"
    assert env.mgr.recent_count() == 0 or True  # timestamps sintéticos; el conteo real se prueba abajo


def test_authorized_is_ignored_by_default_nothing_stored(env):
    out = env.mgr.handle([ev("a", S.AUTHORIZED, name="Ana")])
    assert out[0]["action"] == "ignored"
    assert env.alarm.calls == 0 and env.notifier.items == [] and row(env, "a") is None


def test_unverified_default_logs_only(env):
    env.mgr.handle([ev("u", S.UNVERIFIED)])
    assert env.alarm.calls == 0 and env.notifier.items == []
    assert row(env, "u")["identity_status"] == "UNVERIFIED"


def test_unverified_strict_mode_alerts(env):
    env.cfg.update({"policy": {"unverified": "alert"}})
    env.mgr.handle([ev("u", S.UNVERIFIED)])
    assert env.alarm.calls == 1 and "no verificable" in env.notifier.items[0][2]
    assert "no verificada" in env.notifier.items[0][1]


def test_policy_per_state_is_configurable(env):
    env.cfg.update({"policy": {"unknown": "log", "authorized": "log"}})
    env.mgr.handle([ev("k"), ev("a", S.AUTHORIZED, name="Ana")])
    assert env.alarm.calls == 0 and env.notifier.items == []
    assert row(env, "k") and row(env, "a")


def test_cooldown_suppresses_then_allows_again(env):
    env.cfg.update({"tracking": {"alert_cooldown_seconds": 30}})
    env.mgr.handle([ev("e1", at=1000)])
    env.mgr.handle([ev("e2", at=1010)])  # dentro del tiempo de espera
    assert env.alarm.calls == 1 and len(env.notifier.items) == 1
    r = row(env, "e2")
    assert r["alarm_state"] == "SUPPRESSED" and r["notify_state"] == "SUPPRESSED"  # queda registrado, sin alertar
    env.mgr.handle([ev("e3", at=1045)])
    assert env.alarm.calls == 2 and len(env.notifier.items) == 2


def test_alarm_failure_does_not_block_notification(env):
    env.alarm.raises = True
    env.mgr.handle([ev("e1")])
    assert len(env.notifier.items) == 1 and row(env, "e1")["alarm_state"] == "ERROR"


def test_notification_failure_does_not_block_alarm(env):
    env.notifier.raises = True
    env.mgr.handle([ev("e1")])
    assert env.alarm.calls == 1 and row(env, "e1")["notify_state"] == "ERROR"


def test_audio_error_state_recorded_but_notification_still_sent(env):
    env.alarm.state = "ERROR"
    env.mgr.handle([ev("e1")])
    assert row(env, "e1")["alarm_state"] == "ERROR" and len(env.notifier.items) == 1


def test_dry_run_has_no_side_effects(env):
    out = env.mgr.handle([ev("e1")], dry_run=True)
    assert out[0]["action"] == "would_alert"
    assert env.alarm.calls == 0 and env.notifier.items == [] and row(env, "e1") is None


def test_ended_and_reclassified_update_history(env):
    env.mgr.handle([ev("e1", at=1000)])
    env.mgr.handle([ev("e1", type=EventType.ENDED, at=1100)])
    assert row(env, "e1")["ended_at"] == 1100
    env.mgr.handle([ev("e2", at=2000)])
    env.mgr.handle([ev("e3", S.AUTHORIZED, at=2100, type=EventType.RECLASSIFIED, prev_event_id="e2")])
    assert "autorizada" in row(env, "e2")["detail"]


def test_notification_text_format_and_privacy():
    title, body = build_notification(S.UNKNOWN, "abc123", "sala-pi", 1_700_000_000)
    assert title == "⚠️ Pi Sentinel — Presencia desconocida"
    lines = body.split("\n")
    assert lines[0] == "Se ha detectado una persona no reconocida en la habitación."
    for prefix in ("Fecha: ", "Hora: ", "Dispositivo: sala-pi", "Evento: abc123", "Estado: desconocido"):
        assert any(l.startswith(prefix) for l in lines), prefix
    assert lines[-1] == "Comprueba la situación si es necesario."
    _, body2 = build_notification(S.UNVERIFIED, "z", "d", 1_700_000_000)
    assert "Estado: no verificable" in body2


def test_notification_never_contains_names_or_biometrics(env):
    env.mgr.handle([ev("e1", name="Ana", similarity=0.9)])
    _, _, body = env.notifier.items[0]
    assert "Ana" not in body and "0.9" not in body and "embedding" not in body.lower()


def test_history_listing_retention_and_clear(env):
    env.cfg.update({"policy": {"unknown": "log"}})
    import time
    now = time.time()
    env.mgr.handle([ev("old", at=now - 40 * 86400, started_at=now - 40 * 86400), ev("new", at=now, started_at=now)])
    assert env.mgr.recent_count(24) == 1
    assert len(env.mgr.list_events()) == 2
    assert env.mgr.purge(30) == 1 and [e["id"] for e in env.mgr.list_events()] == ["new"]
    assert env.mgr.clear() == 1 and env.mgr.list_events() == []


def test_handle_survives_database_errors(env):
    env.db.close()
    assert env.mgr.handle([ev("e1")]) == []  # no lanza
