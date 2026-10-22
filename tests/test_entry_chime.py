"""Sonido de entrada: suena al confirmarse una presencia, aparte de la alarma y de ntfy."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import ConfigStore, EntrySettings, PROJECT_ROOT
from app.database import Database
from app.domain import IdentityStatus as S
from app.event_manager import EventManager
from app.presence_tracker import EventType, TrackerEvent
from app.sound_alert import SoundAlert, build_pcm, read_wav
from conftest import FakeAudio


def ev(eid, status, at=1000.0):
    return TrackerEvent(EventType.CONFIRMED, eid, "t", status, at, at - 3, 5, None)


class Rig:
    def __init__(self, tmp_path):
        self.db = Database(tmp_path / "c.db")
        self.cfg = ConfigStore(self.db)
        self.audio = FakeAudio()
        mk = lambda sec, be: SoundAlert(self.cfg.get, tmp_path / "b", tmp_path / "u", tmp_path / "t", be, section=sec)
        self.alarm_audio, self.chime_audio = FakeAudio(), FakeAudio()
        self.alarm, self.chime = mk("alarm", self.alarm_audio), mk("entry", self.chime_audio)
        self.alarm.ensure_default()
        self.notifier = SimpleNamespace(enqueue_alert=lambda *a, **k: "QUEUED")
        self.mgr = EventManager(self.db, self.cfg.get, self.alarm, self.notifier, chime=self.chime)

    def handle(self, *evs, **kw):
        out = self.mgr.handle(list(evs), **kw)
        self.alarm.join(); self.chime.join()
        return out


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_chime_plays_for_authorized_person_even_though_policy_ignores_it(rig):
    out = rig.handle(ev("a", S.AUTHORIZED))
    assert out[0]["action"] == "ignored" and out[0]["chime"] == "TRIGGERED"
    assert len(rig.chime_audio.played) == 1 and rig.alarm_audio.played == []


def test_unknown_person_gets_alarm_not_chime_on_top(rig):
    out = rig.handle(ev("u", S.UNKNOWN))
    assert out[0]["alarm"] == "TRIGGERED" and out[0]["chime"] == "SKIPPED"
    assert len(rig.alarm_audio.played) == 1 and rig.chime_audio.played == []


def test_unknown_with_alarm_disabled_falls_back_to_chime(rig):
    rig.cfg.update({"alarm": {"enabled": False}})
    out = rig.handle(ev("u", S.UNKNOWN))
    assert out[0]["alarm"] == "DISABLED" and out[0]["chime"] == "TRIGGERED"


def test_chime_per_status_toggles_and_master_switch(rig):
    rig.cfg.update({"entry": {"on_authorized": False}})
    assert rig.handle(ev("a", S.AUTHORIZED))[0]["chime"] == "SKIPPED"
    rig.cfg.update({"entry": {"on_authorized": True, "enabled": False}})
    assert rig.handle(ev("b", S.AUTHORIZED, at=2000))[0]["chime"] == "DISABLED"
    assert rig.chime_audio.played == []


def test_chime_in_test_mode_does_nothing(rig):
    out = rig.handle(ev("a", S.AUTHORIZED), dry_run=True)
    assert "chime" not in out[0] and rig.chime_audio.played == []


def test_chime_is_rate_limited(rig):
    rig.handle(ev("a", S.AUTHORIZED))
    out = rig.handle(ev("b", S.AUTHORIZED, at=1001))
    assert out[0]["chime"] == "RATE_LIMITED" and len(rig.chime_audio.played) == 1


def test_chime_audio_error_does_not_break_alerts(tmp_path):
    r = Rig(tmp_path)
    r.chime._backend = FakeAudio(fail_check=True)
    r.cfg.update({"alarm": {"enabled": False}})
    out = r.handle(ev("u", S.UNKNOWN))
    assert out[0]["chime"] == "ERROR" and out[0]["notify"] == "QUEUED"
    assert r.db.query_one("SELECT identity_status FROM events WHERE id='u'")["identity_status"] == "UNKNOWN"


def test_mute_is_independent_per_channel_object(rig):
    rig.chime.mute(5)
    assert rig.handle(ev("a", S.AUTHORIZED))[0]["chime"] == "MUTED"


def test_bundled_sound_is_listed_default_for_entry_and_selectable(rig):
    names = [s["name"] for s in rig.chime.list_sounds()]
    assert "default" in names and "entrada.wav" in names
    assert EntrySettings().sound == "entrada.wav"
    assert rig.chime.resolve("entrada.wav").name == "entrada.wav"
    rig.cfg.update({"entry": {"sound": "default"}})  # también puede usarse la sirena


def test_bundled_chime_file_is_valid_short_and_cc0_documented():
    ch, rate, pcm = read_wav(PROJECT_ROOT / "sounds" / "entrada.wav")
    assert ch == 1 and len(pcm) / 2 / rate < 3.0
    assert "CC0" in (PROJECT_ROOT / "sounds" / "LICENCIAS.md").read_text(encoding="utf-8")


def test_missing_bundled_chime_is_regenerated(tmp_path):
    sa = SoundAlert(lambda: SimpleNamespace(entry=EntrySettings()), tmp_path / "b", tmp_path / "u", tmp_path / "t", FakeAudio(), section="entry")
    sa.ensure_default()
    assert (tmp_path / "b" / "entrada.wav").is_file()
    assert sa.trigger() == "TRIGGERED"; sa.join()


def test_builtin_sounds_cannot_be_deleted_but_user_ones_can(rig, tmp_path):
    from app.sound_alert import AudioError
    with pytest.raises(AudioError):
        rig.chime.delete_user_sound("entrada.wav")
    src = (PROJECT_ROOT / "sounds" / "entrada.wav").read_bytes()
    rig.chime.add_user_sound("mio.wav", src)
    rig.chime.delete_user_sound("mio.wav")


def test_quiet_sounds_are_peak_normalized_but_never_clipped():
    quiet = (np.sin(np.linspace(0, 200, 4000)) * 8000).astype("<i2").tobytes()
    out = np.frombuffer(build_pcm(quiet, 1, 4000, 1.0, 1, 10, normalize=True), "<i2")
    assert 0.85 * 32767 < np.abs(out.astype(int)).max() <= 32767
    very_quiet = (np.sin(np.linspace(0, 200, 4000)) * 1000).astype("<i2").tobytes()  # tope ×8: no se amplifica ruido sin límite
    out_vq = np.frombuffer(build_pcm(very_quiet, 1, 4000, 1.0, 1, 10, normalize=True), "<i2")
    assert np.abs(out_vq.astype(int)).max() <= 8100
    loud = (np.sin(np.linspace(0, 200, 4000)) * 30000).astype("<i2").tobytes()
    out2 = np.frombuffer(build_pcm(loud, 1, 4000, 1.0, 1, 10, normalize=True), "<i2")
    assert np.abs(out2.astype(int)).max() <= 30001  # no se amplifica lo que ya suena fuerte
    silent = np.zeros(100, "<i2").tobytes()
    assert not np.any(np.frombuffer(build_pcm(silent, 1, 4000, 1.0, 1, 10, normalize=True), "<i2"))
