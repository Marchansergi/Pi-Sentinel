"""SubprocessBackend con reproductores falsos (scripts reales en el PATH que registran lo que reciben)."""
import json
import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import AlarmSettings, EntrySettings
from app.sound_alert import AudioError, SoundAlert, SubprocessBackend, audio_env, available_players


def fake_player(bindir: Path, name: str, exit_code=0, stderr=""):
    log = bindir / f"{name}.log"
    script = bindir / name
    script.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'xdg': os.environ.get('XDG_RUNTIME_DIR'), 'pulse': os.environ.get('PULSE_SERVER')}}) + '\\n')\n"
        f"sys.stderr.write({stderr!r})\nsys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return log


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    return d


def calls(log: Path):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def wavfile(tmp_path):
    from app.sound_alert import generate_default_wav
    p = tmp_path / "x.wav"
    generate_default_wav(p, seconds=0.2)
    return p


def test_pipewire_player_is_preferred_over_aplay(bindir, tmp_path):
    pw, ap = fake_player(bindir, "pw-play"), fake_player(bindir, "aplay")
    assert available_players()[0] == "pw-play"
    SubprocessBackend().play(wavfile(tmp_path), threading.Event())
    assert len(calls(pw)) == 1 and calls(ap) == []


def test_falls_back_to_next_player_when_preferred_missing(bindir, tmp_path):
    ap = fake_player(bindir, "aplay")
    SubprocessBackend().play(wavfile(tmp_path), threading.Event())
    assert calls(ap)[0]["argv"][0] == "-q"


def test_player_can_be_pinned_and_missing_pinned_player_is_clear(bindir, tmp_path):
    pw, ap = fake_player(bindir, "pw-play"), fake_player(bindir, "aplay")
    SubprocessBackend().play(wavfile(tmp_path), threading.Event(), "", "aplay")
    assert len(calls(ap)) == 1 and calls(pw) == []
    with pytest.raises(AudioError, match="paplay.*no está instalado"):
        SubprocessBackend().play(wavfile(tmp_path), threading.Event(), "", "paplay")


@pytest.mark.parametrize("player,expected", [("pw-play", "--target=bluez_output.AA_BB"), ("paplay", "--device=bluez_output.AA_BB"), ("aplay", "bluez_output.AA_BB")])
def test_device_argument_per_player(bindir, tmp_path, player, expected):
    log = fake_player(bindir, player)
    SubprocessBackend().play(wavfile(tmp_path), threading.Event(), "bluez_output.AA_BB", player)
    argv = calls(log)[0]["argv"]
    assert expected in argv and argv[-1].endswith("x.wav")


def test_player_failure_message_includes_stderr(bindir, tmp_path):
    fake_player(bindir, "pw-play", exit_code=1, stderr="connection refused")
    with pytest.raises(AudioError, match="pw-play falló.*connection refused"):
        SubprocessBackend().play(wavfile(tmp_path), threading.Event())


def test_audio_env_points_to_user_session_when_run_as_service(monkeypatch, tmp_path):
    run = tmp_path / f"run"
    (run / "pulse").mkdir(parents=True)
    (run / "pulse" / "native").touch()
    monkeypatch.delenv("PULSE_SERVER", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    env = audio_env()
    assert env["PULSE_SERVER"] == f"unix:{run}/pulse/native"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setattr(os, "getuid", lambda: 4242)
    monkeypatch.setattr(os.path, "isdir", lambda p: p == "/run/user/4242")
    assert audio_env()["XDG_RUNTIME_DIR"] == "/run/user/4242"


def mk(tmp_path, backend, **alarm):
    settings = SimpleNamespace(alarm=AlarmSettings(lead_in_seconds=0.0, **alarm), entry=EntrySettings())
    sa = SoundAlert(lambda: settings, tmp_path / "b", tmp_path / "u", tmp_path / "t", backend)
    sa.ensure_default()
    return sa


def test_test_button_reports_real_playback_failure_not_just_thread_start(bindir, tmp_path):
    fake_player(bindir, "pw-play", exit_code=1, stderr="no sink")
    sa = mk(tmp_path, SubprocessBackend())
    assert sa.trigger(force=True, wait=3.0) == "ERROR"
    assert "no sink" in sa.last_error


def test_test_button_ok_with_working_player_and_uses_shared_target(bindir, tmp_path):
    log = fake_player(bindir, "paplay")
    sa = mk(tmp_path, SubprocessBackend(), player="paplay", audio_device="mi_altavoz")
    assert sa.trigger(force=True, wait=3.0) == "TRIGGERED"
    sa.join()
    assert "--device=mi_altavoz" in calls(log)[0]["argv"]
    # el sonido de entrada usa el mismo reproductor/dispositivo que la alarma
    settings = SimpleNamespace(alarm=AlarmSettings(lead_in_seconds=0.0, player="paplay", audio_device="mi_altavoz"), entry=EntrySettings())
    chime = SoundAlert(lambda: settings, tmp_path / "b", tmp_path / "u", tmp_path / "t", SubprocessBackend(), section="entry")
    assert chime.trigger(force=True, wait=3.0) == "TRIGGERED"
    chime.join()
    assert len(calls(log)) == 2


def test_lead_in_silence_is_prepended(tmp_path):
    import wave
    seen = {}

    class Spy:
        def check(self, player="auto"): return "spy"
        def play(self, path, stop, device="", player="auto"):
            with wave.open(str(path)) as w:
                seen["frames"], seen["rate"] = w.getnframes(), w.getframerate()
                seen["head"] = w.readframes(100)

    settings = SimpleNamespace(alarm=AlarmSettings(lead_in_seconds=1.0, repetitions=1, duration_seconds=1.0), entry=EntrySettings())
    sa = SoundAlert(lambda: settings, tmp_path / "b", tmp_path / "u", tmp_path / "t", Spy())
    sa.ensure_default()
    sa.trigger(force=True); sa.join()
    assert seen["frames"] == seen["rate"] * 2  # 1 s de silencio + 1 s de sonido
    assert seen["head"] == b"\x00" * 200
