"""Reconocimiento facial (lógica) y perfiles.

Los embeddings son vectores aleatorios controlados: demuestran la lógica de umbrales y estados,
NO la precisión de SFace con personas reales.
"""
import numpy as np
import pytest

from app.authorized_people import AuthorizedPeople, ProfileError
from app.config import FaceSettings, PROJECT_ROOT
from app.database import Database
from app.domain import IdentityStatus
from app.face_recognition import (
    DetectedFace, FaceEngine, GalleryEntry, assess_quality, estimate_yaw, match_embedding,
)
from conftest import blank_frame, noisy, requires_models, unit


def gallery_of(*people):
    return [GalleryEntry(pid, name, np.stack(embs)) for pid, name, embs in people]


# ------------------------------------------------------------------ coincidencia


def test_authorized_identity_is_recognized():
    owner = unit(1)
    g = gallery_of((1, "Ana", [owner, noisy(owner, 0.05, 1)]))
    m = match_embedding(noisy(owner, 0.1, 2), g, 0.363)
    assert m.matched and m.name == "Ana" and m.person_id == 1 and m.best_score > 0.9


def test_unknown_identity_is_not_matched():
    g = gallery_of((1, "Ana", [unit(1)]))
    m = match_embedding(unit(99), g, 0.363)
    assert not m.matched and m.name is None
    assert m.best_score is not None and m.best_score < 0.363  # se informa la puntuación aunque no coincida


def test_threshold_is_configurable():
    owner = unit(1)
    probe = noisy(owner, 0.9, 5)  # parecido moderado
    g = gallery_of((1, "Ana", [owner]))
    score = match_embedding(probe, g, 0.0).best_score
    assert 0.3 < score < 0.95
    assert match_embedding(probe, g, score - 0.01).matched
    assert not match_embedding(probe, g, score + 0.01).matched


def test_empty_gallery_never_matches():
    m = match_embedding(unit(1), [], 0.363)
    assert not m.matched and m.best_score is None


def test_best_of_several_people_is_chosen():
    a, b = unit(1), unit(2)
    g = gallery_of((1, "Ana", [a]), (2, "Beto", [b]))
    assert match_embedding(noisy(b, 0.05), g, 0.363).name == "Beto"


# ------------------------------------------------------------------ calidad


def landmarks(w=100, nose_dx=0.0):
    # ojo der., ojo izq., nariz, boca der., boca izq. (en un recuadro de w×w)
    return np.array([[0.3 * w, 0.35 * w], [0.7 * w, 0.35 * w], [0.5 * w + nose_dx * 0.4 * w, 0.55 * w],
                     [0.35 * w, 0.75 * w], [0.65 * w, 0.75 * w]], np.float32)


def textured(w=640, h=480, v=120, seed=0):
    rng = np.random.RandomState(seed)
    img = np.clip(v + rng.randn(h, w, 3) * 40, 0, 255).astype(np.uint8)
    return img


def face_at(x, y, size, score=0.95, nose_dx=0.0):
    lm = landmarks(size, nose_dx) + np.array([x, y], np.float32)
    return DetectedFace((x, y, x + size, y + size), lm, score, None)


CFG = FaceSettings()


def test_good_quality_face_is_ok():
    q = assess_quality(textured(), face_at(200, 150, 150), CFG)
    assert q.ok, q.reasons


@pytest.mark.parametrize("kwargs,frame,needle", [
    (dict(size=30), None, "pequeña"),
    (dict(size=150, score=0.5), None, "poco fiable"),
    (dict(size=150, nose_dx=1.5), None, "girada"),
])
def test_bad_faces_are_rejected(kwargs, frame, needle):
    f = face_at(200, 150, kwargs.get("size"), kwargs.get("score", 0.95), kwargs.get("nose_dx", 0.0))
    q = assess_quality(frame if frame is not None else textured(), f, CFG)
    assert not q.ok and any(needle in r for r in q.reasons)


def test_blurry_dark_and_cropped_faces_are_rejected():
    assert any("borrosa" in r for r in assess_quality(blank_frame(value=120), face_at(200, 150, 150), CFG).reasons)
    assert any("insuficiente" in r for r in assess_quality(textured(v=10), face_at(200, 150, 150), CFG).reasons)
    assert any("cortada" in r for r in assess_quality(textured(), face_at(0, 100, 150), CFG).reasons)


def test_yaw_estimate_sign_and_magnitude():
    assert abs(estimate_yaw(landmarks())) < 0.01
    assert estimate_yaw(landmarks(nose_dx=0.8)) > 0.2
    assert estimate_yaw(landmarks(nose_dx=-0.8)) < -0.2


# ------------------------------------------------------------------ motor (con detector/embedder simulados)


class StubEngine(FaceEngine):
    """FaceEngine real, pero con detección e incrustación sustituidas por datos controlados."""

    def __init__(self, faces, embedding):
        super().__init__("x", "y")
        self.loaded = True
        self._faces, self._emb = faces, embedding

    def _detect_locked(self, frame):
        return list(self._faces)

    def _embed_locked(self, frame, face):
        return self._emb


def test_engine_classifies_authorized_unknown_and_unverified():
    owner = unit(1)
    g = gallery_of((1, "Ana", [owner]))
    frame = textured()
    good = face_at(200, 150, 150)

    r = StubEngine([good], owner).analyze(frame, g, CFG)
    assert r.ok and r.faces[0].status is IdentityStatus.AUTHORIZED and r.faces[0].name == "Ana"

    r = StubEngine([good], unit(77)).analyze(frame, g, CFG)
    assert r.faces[0].status is IdentityStatus.UNKNOWN and r.faces[0].name is None

    tiny = face_at(200, 150, 20)  # rostro demasiado pequeño → no verificable (NO desconocido)
    r = StubEngine([tiny], unit(77)).analyze(frame, g, CFG)
    assert r.faces[0].status is IdentityStatus.UNVERIFIED and r.faces[0].reasons

    r = StubEngine([good], owner).analyze(blank_frame(value=120), g, CFG)  # imagen borrosa
    assert r.faces[0].status is IdentityStatus.UNVERIFIED


def test_engine_no_faces_returns_ok_and_empty():
    r = StubEngine([], unit(1)).analyze(textured(), [], CFG)
    assert r.ok and r.faces == ()


def test_engine_errors_are_reported_not_raised():
    class Broken(StubEngine):
        def _detect_locked(self, frame):
            raise RuntimeError("fallo del modelo")

    r = Broken([], unit(1)).analyze(textured(), [], CFG)
    assert not r.ok and "fallo" in r.error


def test_engine_limits_embeddings_per_frame():
    faces = [face_at(20 + i * 120, 150, 100) for i in range(5)]
    r = StubEngine(faces, unit(77)).analyze(textured(), gallery_of((1, "Ana", [unit(1)])), CFG, max_embeds=2)
    st = [f.status for f in r.faces]
    assert st.count(IdentityStatus.UNKNOWN) == 2 and st.count(IdentityStatus.UNVERIFIED) == 3


def test_extract_sample_requires_exactly_one_good_face():
    f = textured()
    emb, q, why = StubEngine([], unit(1)).extract_sample(f, CFG)
    assert emb is None and "ninguna cara" in why
    emb, q, why = StubEngine([face_at(50, 150, 120), face_at(300, 150, 120)], unit(1)).extract_sample(f, CFG)
    assert emb is None and "más de una" in why
    emb, q, why = StubEngine([face_at(200, 150, 20)], unit(1)).extract_sample(f, CFG)
    assert emb is None and "rechazada" in why
    emb, q, why = StubEngine([face_at(200, 150, 150)], unit(1)).extract_sample(f, CFG)
    assert emb is not None and why is None


@requires_models
def test_real_models_load_and_handle_faceless_image():
    eng = FaceEngine(PROJECT_ROOT / "models/face_detection_yunet_2023mar.onnx",
                     PROJECT_ROOT / "models/face_recognition_sface_2021dec.onnx")
    r = eng.analyze(textured(), [], CFG)
    assert r.ok and r.faces == ()


# ------------------------------------------------------------------ perfiles


@pytest.fixture
def people(tmp_path):
    db = Database(tmp_path / "t.db")
    yield AuthorizedPeople(db, lambda: 3)
    db.close()


def add(people, pid, n, base=1):
    for i in range(n):
        people.add_sample(pid, noisy(unit(base), 0.05, i), 0.8)


def test_create_list_rename_and_duplicates(people):
    p = people.create("Ana")
    assert p.enabled and p.sample_count == 0 and not p.ready
    with pytest.raises(ProfileError):
        people.create("ana")  # nombre repetido (sin distinguir mayúsculas)
    assert people.rename(p.id, "Ana María").name == "Ana María"
    with pytest.raises(ProfileError):
        people.create("<script>")


def test_profile_ready_only_with_enough_samples_and_enabled(people):
    p = people.create("Ana")
    add(people, p.id, 2)
    assert people.gallery() == []  # insuficientes muestras → no se usa
    add(people, p.id, 1)
    assert [g.name for g in people.gallery()] == ["Ana"]
    people.set_enabled(p.id, False)
    assert people.gallery() == []  # desactivado temporalmente
    people.set_enabled(p.id, True)
    assert len(people.gallery()) == 1


def test_delete_sample_updates_gallery_cache(people):
    p = people.create("Ana")
    add(people, p.id, 3)
    assert people.gallery()
    people.delete_sample(p.id, people.samples(p.id)[0].id)
    assert people.gallery() == []
    with pytest.raises(ProfileError):
        people.delete_sample(p.id, 99999)


def test_deleted_profile_is_no_longer_recognized_and_samples_removed(people):
    owner = unit(1)
    p = people.create("Ana")
    add(people, p.id, 3)
    assert match_embedding(owner, people.gallery(), 0.363).matched
    people.delete_person(p.id)
    assert not match_embedding(owner, people.gallery(), 0.363).matched
    assert people._db.query_one("SELECT COUNT(*) AS n FROM face_samples")["n"] == 0  # borrado en cascada
    with pytest.raises(ProfileError):
        people.get(p.id)


def test_delete_all_removes_everything(people):
    for n in ("Ana", "Beto"):
        p = people.create(n)
        add(people, p.id, 3)
    assert people.delete_all() == 2
    assert people.list_people() == [] and people.gallery() == []
    assert people._db.query_one("SELECT COUNT(*) AS n FROM face_samples")["n"] == 0


def test_invalid_embeddings_rejected(people):
    p = people.create("Ana")
    with pytest.raises(ProfileError):
        people.add_sample(p.id, np.zeros(10), 0.5)
    with pytest.raises(ProfileError):
        people.add_sample(p.id, np.full(128, np.nan), 0.5)
    with pytest.raises(ProfileError):
        people.add_sample(p.id, unit(1), 0.5, pose="boca-abajo")


def test_embeddings_are_persisted_and_database_is_private(tmp_path):
    db = Database(tmp_path / "x" / "p.db")
    ppl = AuthorizedPeople(db, lambda: 1)
    p = ppl.create("Ana")
    ppl.add_sample(p.id, unit(4), 0.9)
    db.close()
    db2 = Database(tmp_path / "x" / "p.db")
    assert np.allclose(AuthorizedPeople(db2, lambda: 1).embeddings_for(p.id)[0], unit(4), atol=1e-6)
    import os, stat
    assert stat.S_IMODE(os.stat(tmp_path / "x" / "p.db").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "x").st_mode) == 0o700
    db2.close()
