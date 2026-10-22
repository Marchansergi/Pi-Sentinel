import numpy as np

from app.person_detector import MotionGate, PersonDetector, decode_yolox, _grid
from conftest import blank_frame, requires_models
from app.config import PROJECT_ROOT

MODEL = PROJECT_ROOT / "models" / "object_detection_yolox_2022nov.onnx"


class FakeNet:
    """Simula la salida cruda de YOLOX con una persona en una ancla concreta."""

    def __init__(self, size, cx, cy, w, h, score, cls=0):
        grid, strides = _grid(size)
        raw = np.zeros((grid.shape[0], 85), np.float32)
        raw[:, 2:4] = np.log(1.0)  # cajas de 1px: irrelevantes
        raw[:, 4] = 0.0
        stride = 8.0
        gx, gy = int(cx // stride), int(cy // stride)
        idx = gy * (size // 8) + gx
        raw[idx, 0] = cx / stride - gx
        raw[idx, 1] = cy / stride - gy
        raw[idx, 2] = np.log(w / stride)
        raw[idx, 3] = np.log(h / stride)
        raw[idx, 4] = score
        raw[idx, 5 + cls] = 1.0
        self.raw = raw[None]

    def setInput(self, blob):
        self.blob_shape = blob.shape

    def forward(self):
        return self.raw


def detector_with(net):
    d = PersonDetector(MODEL)
    d._net = net
    return d


def test_decode_geometry():
    size = 416
    net = FakeNet(size, 200, 200, 80, 160, 0.9)
    out = decode_yolox(net.raw[0], size)
    i = int(np.argmax(out[:, 4]))
    assert np.allclose(out[i, :4], [200, 200, 80, 160], atol=1)


def test_detects_a_person_with_confidence_and_region():
    # lienzo 640x480 → 416: r=0.65; persona en (200,200) del lienzo ≈ (307,307) de la imagen
    d = detector_with(FakeNet(416, 200, 200, 80, 160, 0.9))
    r = d.detect(blank_frame(), confidence=0.5, input_size=416)
    assert r.ok and len(r.persons) == 1
    p = r.persons[0]
    assert abs(p.confidence - 0.9) < 1e-3
    assert 0 <= p.x1 < p.x2 <= 639 and 0 <= p.y1 < p.y2 <= 479
    cx, cy = p.center()
    assert abs(cx - 200 / (416 / 640)) < 3 and abs(cy - 200 / (416 / 640)) < 3


def test_confidence_threshold_filters():
    d = detector_with(FakeNet(416, 200, 200, 80, 160, 0.4))
    assert d.detect(blank_frame(), confidence=0.5, input_size=416).persons == ()
    assert len(d.detect(blank_frame(), confidence=0.3, input_size=416).persons) == 1


def test_other_classes_are_not_people():
    d = detector_with(FakeNet(416, 200, 200, 80, 160, 0.95, cls=2))  # «coche»
    r = d.detect(blank_frame(), confidence=0.5, input_size=416)
    assert r.ok and r.persons == ()


def test_no_people_is_ok_not_error():
    net = FakeNet(416, 200, 200, 80, 160, 0.0)
    r = detector_with(net).detect(blank_frame(), confidence=0.5, input_size=416)
    assert r.ok and r.persons == () and r.error is None


def test_invalid_frame_is_an_error_not_empty():
    d = detector_with(FakeNet(416, 200, 200, 80, 160, 0.9))
    for bad in (None, np.zeros((10, 10), np.uint8), np.zeros((0, 0, 3), np.uint8)):
        r = d.detect(bad)
        assert not r.ok and r.error


def test_model_error_is_reported_as_error():
    class Boom:
        def setInput(self, b):
            raise RuntimeError("fallo de inferencia")

        def forward(self):
            raise RuntimeError("fallo de inferencia")

    r = detector_with(Boom()).detect(blank_frame())
    assert not r.ok and "fallo" in r.error


def test_missing_model_is_an_error(tmp_path):
    d = PersonDetector(tmp_path / "no_existe.onnx")
    r = d.detect(blank_frame())
    assert not r.ok and "falta el modelo" in r.error.lower()


def test_unexpected_output_shape_is_error():
    net = FakeNet(416, 200, 200, 80, 160, 0.9)
    r = detector_with(net).detect(blank_frame(), input_size=320)  # el modelo devolvió datos de 416
    assert not r.ok


@requires_models
def test_real_model_blank_image_has_no_people():
    r = PersonDetector(MODEL).detect(blank_frame(), confidence=0.5, input_size=320)
    assert r.ok and r.persons == ()


def test_motion_gate_skips_static_scene_but_not_forever():
    g = MotionGate()
    f = blank_frame()
    assert g.should_run(f, 0.0, max_idle=2.0)  # primera vez siempre
    assert not g.should_run(f, 0.5, max_idle=2.0)  # estática
    assert g.should_run(f, 2.1, max_idle=2.0)  # tope de inactividad
    moved = blank_frame(value=200)
    assert g.should_run(moved, 2.2, max_idle=2.0)  # movimiento
    assert g.should_run(f, 2.3, max_idle=2.0, force=True)  # forzado (presencia activa)
