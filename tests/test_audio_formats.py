"""Formatos de audio admitidos al subir, nombres seguros y sesión de audio cuando se ejecuta como root."""
import os
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import AlarmSettings, EntrySettings
from app.sound_alert import (AudioError, SoundAlert, SubprocessBackend, _candidate_runtime_dirs, audio_diagnostics,
                             load_audio, play_test_tone, sanitize_sound_name, session_runtime_dir)
from conftest import FakeAudio


def make_wav(samples: np.ndarray, rate: int, fmt_tag: int, bits: int, extensible=False) -> bytes:
    ch = samples.shape[1]
    if fmt_tag == 3:
        raw = samples.astype("<f4" if bits == 32 else "<f8").tobytes()
    elif bits == 24:
        v = np.clip(np.round(samples * 8388607), -8388608, 8388607).astype(np.int32).reshape(-1)
        raw = b"".join(struct.pack("<i", int(x))[:3] for x in v)
    elif bits == 32:
        raw = np.clip(np.round(samples * 2147483647), -2147483648, 2147483647).astype("<i4").tobytes()
    else:
        raw = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2").tobytes()
    align = ch * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHH", 0xFFFE, ch, rate, rate * align, align, bits) + struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", fmt_tag) + b"\x00" * 14
    else:
        fmt = struct.pack("<HHIIHH", fmt_tag, ch, rate, rate * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"LIST" + struct.pack("<I", 4) + b"INFO" + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body


def tone(n=4410, ch=1, amp=0.5):
    t = np.arange(n) / 44100
    return np.stack([amp * np.sin(2 * np.pi * 440 * t)] * ch, axis=1)


@pytest.mark.parametrize("tag,bits,ext", [(1, 16, False), (1, 24, False), (1, 32, False), (3, 32, False), (3, 64, False), (1, 24, True), (3, 32, True)])
def test_common_wav_variants_are_decoded(tag, bits, ext):
    ch, rate, pcm, trunc = load_audio(make_wav(tone(), 44100, tag, bits, ext))
    s = np.frombuffer(pcm, "<i2")
    assert ch == 1 and rate == 44100 and not trunc and len(s) == 4410
    assert 15500 < np.abs(s.astype(int)).max() < 17000  # amplitud 0,5 conservada


def test_high_sample_rate_is_resampled_and_multichannel_downmixed():
    ch, rate, pcm, _ = load_audio(make_wav(tone(9600, ch=6), 96000, 3, 32))
    assert ch == 1 and rate == 44100 and abs(len(pcm) // 2 - 4410) <= 2


def test_long_audio_is_truncated_to_60s():
    ch, rate, pcm, trunc = load_audio(make_wav(np.zeros((8000 * 61, 1)), 8000, 1, 16))
    assert trunc and len(pcm) == 8000 * 60 * 2


def test_ffmpeg_size_placeholder_in_data_chunk_is_tolerated():
    data = bytearray(make_wav(tone(), 44100, 1, 16))
    i = data.index(b"data")
    data[i + 4:i + 8] = b"\xff\xff\xff\xff"  # lo que escribe ffmpeg en una tubería
    ch, rate, pcm, _ = load_audio(bytes(data))
    assert len(pcm) == 4410 * 2


def test_unsupported_inputs_give_clear_errors(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda exe: None)
    with pytest.raises(AudioError, match="ffmpeg"):
        load_audio(b"ID3\x03\x00 esto parece un mp3")
    with pytest.raises(AudioError, match="vacío"):
        load_audio(b"")
    adpcm = make_wav(tone(), 44100, 1, 16).replace(struct.pack("<H", 1), struct.pack("<H", 2), 1)
    with pytest.raises(AudioError):
        load_audio(adpcm)


@pytest.mark.parametrize("raw,expected", [("Mi sonido.mp3", "Mi sonido.wav"), ("../../etc/passwd", "passwd.wav"), ("C:\\x\\timbre.WAV", "timbre.wav"), ("ñandú!.ogg", "and.wav")])
def test_sanitize_sound_name(raw, expected):
    assert sanitize_sound_name(raw) == expected


@pytest.mark.parametrize("bad", ["default.wav", "entrada.wav", "...", ""])
def test_reserved_or_empty_names_rejected(bad):
    with pytest.raises(AudioError):
        sanitize_sound_name(bad)


def test_upload_converts_24bit_and_reports_name(tmp_path):
    settings = SimpleNamespace(alarm=AlarmSettings(lead_in_seconds=0), entry=EntrySettings())
    audio = FakeAudio()
    sa = SoundAlert(lambda: settings, tmp_path / "b", tmp_path / "u", tmp_path / "t", audio)
    name, trunc = sa.add_user_sound("grabación 24bit.wav", make_wav(tone(), 44100, 1, 24))
    assert name == "grabaci_n 24bit.wav" and not trunc
    import wave
    with wave.open(str(tmp_path / "u" / name)) as w:
        assert w.getsampwidth() == 2  # se guarda normalizado a 16 bit
    settings.alarm = AlarmSettings(lead_in_seconds=0, sound=name)
    assert sa.trigger(force=True) == "TRIGGERED"
    sa.join()
    assert audio.played


# ------------------------------------------------------------------ reproducción y sesión


class FlakyBackend(SubprocessBackend):
    def __init__(self, fail):
        super().__init__()
        self.fail, self.tried = set(fail), []

    def _play_one(self, player, path, stop, device):
        self.tried.append(player)
        if player in self.fail:
            from app.sound_alert import AudioError as E
            raise E(f"{player} falló")


def test_auto_mode_falls_back_and_remembers_working_player(monkeypatch, tmp_path):
    monkeypatch.setattr("app.sound_alert.available_players", lambda: ["pw-play", "paplay", "aplay"])
    be = FlakyBackend({"pw-play"})
    assert be.play(tmp_path / "x.wav", threading.Event()) == "paplay"
    assert be.tried == ["pw-play", "paplay"]
    be.tried.clear()
    be.play(tmp_path / "x.wav", threading.Event())
    assert be.tried == ["paplay"]  # recuerda el que funcionó


def test_all_players_failing_reports_every_error(monkeypatch, tmp_path):
    monkeypatch.setattr("app.sound_alert.available_players", lambda: ["pw-play", "aplay"])
    with pytest.raises(AudioError, match="pw-play falló.*aplay falló"):
        FlakyBackend({"pw-play", "aplay"}).play(tmp_path / "x.wav", threading.Event())


def test_pinned_player_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda e: f"/usr/bin/{e}")
    be = FlakyBackend({"aplay"})
    with pytest.raises(AudioError):
        be.play(tmp_path / "x.wav", threading.Event(), "", "aplay")
    assert be.tried == ["aplay"]


def test_root_finds_desktop_users_audio_session(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr("glob.glob", lambda pat: ["/run/user/1000", "/run/user/120"])
    existing = {"/run/user/1000", "/run/user/1000/pipewire-0"}
    monkeypatch.setattr(os.path, "isdir", lambda p: p in existing)
    monkeypatch.setattr(os.path, "exists", lambda p: p in existing)
    assert "/run/user/1000" in _candidate_runtime_dirs() and "/run/user/120" not in _candidate_runtime_dirs()
    assert session_runtime_dir() == "/run/user/1000"


def test_sudo_uid_is_preferred(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("SUDO_UID", "1001")
    monkeypatch.setattr(os, "getuid", lambda: 0)
    monkeypatch.setattr("glob.glob", lambda pat: ["/run/user/1000", "/run/user/1001"])
    existing = {"/run/user/1000", "/run/user/1000/pipewire-0", "/run/user/1001", "/run/user/1001/pulse/native"}
    monkeypatch.setattr(os.path, "isdir", lambda p: p in existing)
    monkeypatch.setattr(os.path, "exists", lambda p: p in existing)
    assert session_runtime_dir() == "/run/user/1001"


def test_diagnostics_hints(monkeypatch):
    import app.sound_alert as sa
    monkeypatch.setattr(sa, "available_players", lambda: ["pw-play"])
    monkeypatch.setattr(sa, "session_runtime_dir", lambda: "/run/user/1000")
    monkeypatch.setattr(sa, "list_sinks", lambda: ([{"name": "alsa_output.hdmi", "description": "HDMI", "bluetooth": False},
                                                   {"name": "bluez_output.AA_BB.1", "description": "Altavoz JBL", "bluetooth": True}],
                                                  "alsa_output.hdmi"))
    monkeypatch.setattr(sa.shutil, "which", lambda e: None)
    d = audio_diagnostics("auto", "")
    assert any("Altavoz JBL" in h and "predeterminada" in h for h in d["hints"])
    monkeypatch.setattr(os, "getuid", lambda: 0)
    assert any("root" in h for h in audio_diagnostics("auto", "")["hints"])
    monkeypatch.setattr(sa, "list_sinks", lambda: ([{"name": "alsa_output.hdmi", "description": "HDMI", "bluetooth": False}], "alsa_output.hdmi"))
    assert any("ninguna salida Bluetooth" in h for h in audio_diagnostics("auto", "")["hints"])
    assert any("no existe" in h for h in audio_diagnostics("auto", "bluez_output.XX.1")["hints"])


def test_play_test_tone_reports_real_result(tmp_path):
    ok = play_test_tone(FakeAudio(), tmp_path, "auto", "", 0.0)
    assert ok["ok"] and ok["error"] == ""
    bad = play_test_tone(FakeAudio(fail_play=True), tmp_path, "pw-play", "x", 0.0)
    assert not bad["ok"] and "sin salida" in bad["error"]
    assert list(tmp_path.glob("*.wav")) == []
