import threading
import time
from types import SimpleNamespace

import numpy as np

from app.camera import CameraManager, validate_frame
from conftest import SourceFactory, blank_frame


def cfg(**kw):
    base = dict(backend="opencv", device="0", width=640, height=480, capture_fps=30.0)
    base.update(kw)
    return SimpleNamespace(**base)


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make(factory, **kw):
    return CameraManager(lambda: cfg(), factory, backoff_initial=0.02, backoff_max=0.1, **kw)


def test_validate_frame_rejects_bad_frames():
    assert validate_frame(blank_frame()) is None
    assert validate_frame(None)
    assert validate_frame("x")
    assert validate_frame(np.zeros((480, 640), np.uint8))  # sin canales
    assert validate_frame(np.zeros((480, 640, 3), np.float32))
    assert validate_frame(np.zeros((4, 4, 3), np.uint8))


def test_camera_delivers_frames_and_releases_device():
    f = SourceFactory()
    cam = make(f)
    cam.start()
    assert wait_for(lambda: cam.latest() is not None)
    assert cam.status()["state"] == "ok"
    cam.stop()
    assert f.closes == f.opens == 1  # el dispositivo queda liberado
    assert cam.status()["state"] == "stopped"
    assert cam.latest() is None


def test_camera_unavailable_reports_error_and_recovers():
    f = SourceFactory(fail_opens=3)
    cam = make(f)
    cam.start()
    assert wait_for(lambda: cam.status()["state"] == "error")
    assert "no disponible" in cam.status()["message"]
    assert wait_for(lambda: cam.latest() is not None)  # tras los reintentos se recupera
    assert f.opens >= 4
    cam.stop()


def test_capture_error_triggers_reopen():
    f = SourceFactory()
    cam = make(f)
    cam.start()
    assert wait_for(lambda: cam.latest() is not None)
    f.fail_reads = True
    assert wait_for(lambda: cam.reconnects >= 1 or cam.status()["state"] == "error")
    f.fail_reads = False
    seq = cam.latest().seq if cam.latest() else 0
    assert wait_for(lambda: cam.latest() is not None and cam.latest().seq > seq)
    cam.stop()
    assert f.opens == f.closes


def test_invalid_frames_are_counted_and_discarded():
    bad = np.zeros((480, 640), np.uint8)
    f = SourceFactory(frames=[bad, blank_frame(), bad])
    cam = make(f)
    cam.start()
    assert wait_for(lambda: cam.latest() is not None and cam.invalid_frames >= 2)
    assert validate_frame(cam.latest().image) is None  # nunca se publica un fotograma inválido
    cam.stop()


def test_only_latest_frame_is_kept_no_queue_growth():
    counter = iter(range(10**6))

    def make_frame():
        return blank_frame(value=next(counter) % 255)

    f = SourceFactory(frames=[make_frame])
    cam = make(f)
    cam.start()
    assert wait_for(lambda: cam.frames_total > 20)
    a = cam.latest()
    time.sleep(0.1)
    b = cam.latest()
    assert b.seq > a.seq  # el consumidor lento ve el más reciente, no uno antiguo
    cam.stop()


def test_wait_frame_times_out_when_no_camera():
    cam = make(SourceFactory(fail_opens=10**6))
    cam.start()
    assert cam.wait_frame(0, timeout=0.2) is None
    cam.stop()


def test_stop_is_idempotent_and_thread_safe():
    f = SourceFactory()
    cam = make(f)
    cam.start()
    cam.start()  # segundo start no crea otro hilo
    assert wait_for(lambda: cam.latest() is not None)
    ts = [threading.Thread(target=cam.stop) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    cam.stop()
    assert f.opens == f.closes == 1
