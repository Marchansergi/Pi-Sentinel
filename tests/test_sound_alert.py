import wave
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import AlarmSettings
from app.sound_alert import AudioError, SoundAlert, build_pcm, generate_default_wav, read_wav
from conftest import FakeAudio


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, audio=None, **alarm):
    settings = SimpleNamespace(alarm=AlarmSettings(**alarm))
    clock, wall = Clock(), Clock()
    sa = SoundAlert(lambda: settings, tmp_path / "builtin", tmp_path / "user", tmp_path / "tmp", audio or FakeAudio(), clock=clock, wall=wall)
    sa.ensure_default()
    return sa, settings, clock, wall


def test_alarm_triggers_and_plays(tmp_path):
    audio = FakeAudio()
    sa, *_ = make(tmp_path, audio)
    assert sa.trigger() == "TRIGGERED"
    sa.join()
    assert len(audio.played) == 1 and audio.played[0].endswith(".wav")
    assert sa.status()["last_played_at"] is not None


def test_no_continuous_replay_min_interval(tmp_path):
    audio = FakeAudio()
    sa, settings, clock, _ = make(tmp_path, audio, min_interval_seconds=30)
    assert sa.trigger() == "TRIGGERED"
    sa.join()
    clock.t += 5
    assert sa.trigger() == "RATE_LIMITED"
    clock.t += 30
    assert sa.trigger() == "TRIGGERED"
    sa.join()
    assert len(audio.played) == 2


def test_busy_while_playing(tmp_path):
    sa, *_ = make(tmp_path, FakeAudio(delay=1.0), min_interval_seconds=0)
    assert sa.trigger() == "TRIGGERED"
    assert sa.trigger() == "BUSY"
    sa.shutdown()


def test_temporary_mute_and_unmute(tmp_path):
    sa, _, _, wall = make(tmp_path)
    sa.mute(10)
    assert sa.is_muted() and sa.trigger() == "MUTED"
    wall.t += 11 * 60  # el silencio caduca solo
    assert not sa.is_muted() and sa.trigger() == "TRIGGERED"
    sa.join()
    sa.mute(10)
    sa.mute(None)  # reactivar manualmente
    assert not sa.is_muted()


def test_sound_can_be_disabled_without_affecting_force_test(tmp_path):
    audio = FakeAudio()
    sa, *_ = make(tmp_path, audio, enabled=False)
    assert sa.trigger() == "DISABLED"
    assert sa.trigger(force=True) == "TRIGGERED"  # el botón de prueba sí suena
    sa.join()
    assert len(audio.played) == 1


def test_missing_audio_device_is_a_clear_error_and_does_not_raise(tmp_path):
    sa, *_ = make(tmp_path, FakeAudio(fail_check=True))
    assert sa.trigger() == "ERROR"
    assert "reproductor" in sa.status()["last_error"]
    assert sa.status()["audio_available"] is False


def test_playback_failure_is_captured(tmp_path):
    sa, *_ = make(tmp_path, FakeAudio(fail_play=True))
    assert sa.trigger() == "TRIGGERED"
    sa.join()
    assert "sin salida" in sa.last_error


def test_stop_interrupts_playback(tmp_path):
    sa, *_ = make(tmp_path, FakeAudio(delay=5.0))
    sa.trigger()
    sa.stop()
    sa.join(2)
    assert not sa.playing()


def test_temp_files_are_cleaned(tmp_path):
    sa, *_ = make(tmp_path)
    sa.trigger()
    sa.join()
    assert list((tmp_path / "tmp").glob("*.wav")) == []


def test_volume_duration_and_repetitions_applied():
    pcm = (np.ones(1000, np.int16) * 10000).tobytes()
    out = np.frombuffer(build_pcm(pcm, 1, 1000, volume=0.5, repetitions=3, duration=2.0), dtype="<i2")
    assert len(out) == 2000  # 3 repeticiones (3 s) recortadas a 2 s
    assert abs(int(out[0]) - 5000) <= 1
    full = np.frombuffer(build_pcm(pcm, 1, 1000, 1.0, 2, 10.0), dtype="<i2")
    assert len(full) == 2000 and full[0] == 10000


def test_default_wav_is_valid(tmp_path):
    p = tmp_path / "d.wav"
    generate_default_wav(p)
    ch, rate, pcm = read_wav(p)
    assert ch == 1 and rate == 22050 and len(pcm) > 1000


def test_user_sounds_validation_and_path_safety(tmp_path):
    sa, *_ = make(tmp_path)
    good = tmp_path / "g.wav"
    generate_default_wav(good)
    sa.add_user_sound("mi.wav", good.read_bytes())
    assert {"name": "mi.wav", "builtin": False} in sa.list_sounds()
    with pytest.raises(AudioError):
        sa.add_user_sound("malo.wav", b"RIFF\x00\x00\x00\x00WAVEbasura")
    with pytest.raises(AudioError):
        sa.add_user_sound("grande.wav", b"0" * (9 * 1024 * 1024))
    with pytest.raises(AudioError):
        sa.resolve("../../etc/passwd")
    with pytest.raises(AudioError):
        sa.delete_user_sound("default")
    sa.delete_user_sound("mi.wav")


def test_8bit_wav_is_accepted_and_converted(tmp_path):
    p = tmp_path / "8bit.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(bytes([128, 200, 56, 128] * 200))
    ch, rate, pcm = read_wav(p)
    assert ch == 1 and rate == 8000 and len(pcm) == 1600
    s = np.frombuffer(pcm, "<i2")
    assert s[1] > 15000 and s[2] < -15000  # 200 → positivo, 56 → negativo


def test_alarm_setting_names_are_validated():
    for bad in ("../x.wav", "a/b.wav", "x.mp3", ".."):
        with pytest.raises(ValueError):
            AlarmSettings(sound=bad)
    assert AlarmSettings(sound="sirena 2.wav").sound == "sirena 2.wav"
