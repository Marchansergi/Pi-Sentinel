"""Gestión de personas autorizadas y sus muestras faciales (solo embeddings, nunca imágenes)."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import numpy as np

from .database import Database
from .face_recognition import EMBED_DIM, GalleryEntry, l2_normalize

log = logging.getLogger(__name__)

POSES = ("any", "frontal", "lateral")
MAX_SAMPLES_PER_PERSON = 30
MAX_PEOPLE = 20


class ProfileError(ValueError):
    """Error de validación mostrable al usuario."""


@dataclass(frozen=True)
class SampleInfo:
    id: int
    quality: float
    pose: str
    created_at: float


@dataclass(frozen=True)
class PersonInfo:
    id: int
    name: str
    enabled: bool
    sample_count: int
    ready: bool  # tiene las muestras mínimas para ser usado en reconocimiento
    created_at: float
    updated_at: float


_NAME_RE = re.compile(r"^[\wáéíóúüñÁÉÍÓÚÜÑ .'-]{1,40}$")


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ProfileError("El nombre debe tener entre 1 y 40 caracteres (letras, números, espacios, . ' -).")
    return name


class AuthorizedPeople:
    def __init__(self, db: Database, min_samples_getter=lambda: 5, clock=time.time) -> None:
        self._db = db
        self._min = min_samples_getter
        self._clock = clock
        self._lock = threading.RLock()
        self._gallery_cache: list[GalleryEntry] | None = None

    # ------------------------------------------------------------------ consulta

    def _invalidate(self) -> None:
        self._gallery_cache = None

    def list_people(self) -> list[PersonInfo]:
        rows = self._db.query(
            "SELECT p.*, (SELECT COUNT(*) FROM face_samples s WHERE s.person_id=p.id) AS n "
            "FROM people p ORDER BY p.created_at"
        )
        return [
            PersonInfo(r["id"], r["name"], bool(r["enabled"]), r["n"], r["n"] >= self._min(), r["created_at"], r["updated_at"])
            for r in rows
        ]

    def get(self, person_id: int) -> PersonInfo:
        for p in self.list_people():
            if p.id == person_id:
                return p
        raise ProfileError("La persona no existe.")

    def samples(self, person_id: int) -> list[SampleInfo]:
        self.get(person_id)
        rows = self._db.query(
            "SELECT id,quality,pose,created_at FROM face_samples WHERE person_id=? ORDER BY id", (person_id,)
        )
        return [SampleInfo(r["id"], r["quality"], r["pose"], r["created_at"]) for r in rows]

    def gallery(self) -> list[GalleryEntry]:
        """Perfiles activos y con muestras suficientes. Con caché invalidada en cada cambio."""
        with self._lock:
            if self._gallery_cache is not None:
                return list(self._gallery_cache)
            out: list[GalleryEntry] = []
            for p in self.list_people():
                if not p.enabled or not p.ready:
                    continue
                rows = self._db.query("SELECT embedding FROM face_samples WHERE person_id=?", (p.id,))
                embs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]) if rows else np.zeros((0, EMBED_DIM), np.float32)
                out.append(GalleryEntry(p.id, p.name, embs))
            self._gallery_cache = out
            return list(out)

    def embeddings_for(self, person_id: int) -> np.ndarray:
        rows = self._db.query("SELECT embedding FROM face_samples WHERE person_id=?", (person_id,))
        if not rows:
            return np.zeros((0, EMBED_DIM), np.float32)
        return np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    def active_profile_count(self) -> int:
        return len(self.gallery())

    # ------------------------------------------------------------------ modificación

    def create(self, name: str) -> PersonInfo:
        name = clean_name(name)
        with self._lock:
            if len(self.list_people()) >= MAX_PEOPLE:
                raise ProfileError(f"Se alcanzó el máximo de {MAX_PEOPLE} perfiles.")
            now = self._clock()
            try:
                cur = self._db.execute(
                    "INSERT INTO people(name,enabled,created_at,updated_at) VALUES(?,1,?,?)", (name, now, now)
                )
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise ProfileError("Ya existe una persona con ese nombre.") from exc
                raise
            self._invalidate()
            return self.get(cur.lastrowid)

    def rename(self, person_id: int, name: str) -> PersonInfo:
        name = clean_name(name)
        with self._lock:
            self.get(person_id)
            try:
                self._db.execute("UPDATE people SET name=?, updated_at=? WHERE id=?", (name, self._clock(), person_id))
            except Exception as exc:
                if "UNIQUE" in str(exc):
                    raise ProfileError("Ya existe una persona con ese nombre.") from exc
                raise
            self._invalidate()
            return self.get(person_id)

    def set_enabled(self, person_id: int, enabled: bool) -> PersonInfo:
        with self._lock:
            self.get(person_id)
            self._db.execute("UPDATE people SET enabled=?, updated_at=? WHERE id=?", (int(enabled), self._clock(), person_id))
            self._invalidate()
            return self.get(person_id)

    def add_sample(self, person_id: int, embedding: np.ndarray, quality: float, pose: str = "any") -> SampleInfo:
        if pose not in POSES:
            raise ProfileError("Pose no válida.")
        emb = l2_normalize(embedding).astype(np.float32)
        if emb.shape != (EMBED_DIM,) or not np.all(np.isfinite(emb)):
            raise ProfileError("Embedding facial no válido.")
        with self._lock:
            self.get(person_id)
            if len(self.samples(person_id)) >= MAX_SAMPLES_PER_PERSON:
                raise ProfileError(f"Máximo de {MAX_SAMPLES_PER_PERSON} muestras por persona. Elimina alguna antes de añadir más.")
            now = self._clock()
            cur = self._db.execute(
                "INSERT INTO face_samples(person_id,embedding,dim,quality,pose,created_at) VALUES(?,?,?,?,?,?)",
                (person_id, emb.tobytes(), EMBED_DIM, float(quality), pose, now),
            )
            self._db.execute("UPDATE people SET updated_at=? WHERE id=?", (now, person_id))
            self._invalidate()
            return SampleInfo(cur.lastrowid, float(quality), pose, now)

    def delete_sample(self, person_id: int, sample_id: int) -> None:
        with self._lock:
            cur = self._db.execute("DELETE FROM face_samples WHERE id=? AND person_id=?", (sample_id, person_id))
            if cur.rowcount == 0:
                raise ProfileError("La muestra no existe.")
            self._db.execute("UPDATE people SET updated_at=? WHERE id=?", (self._clock(), person_id))
            self._invalidate()

    def delete_person(self, person_id: int) -> None:
        """Elimina el perfil y TODAS sus muestras (borrado en cascada)."""
        with self._lock:
            self.get(person_id)
            self._db.execute("DELETE FROM people WHERE id=?", (person_id,))
            self._invalidate()
            log.info("Perfil %s eliminado con sus muestras", person_id)

    def delete_all(self) -> int:
        with self._lock:
            n = len(self.list_people())
            self._db.execute("DELETE FROM face_samples")
            self._db.execute("DELETE FROM people")
            self._invalidate()
            try:
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.execute("VACUUM")  # elimina restos de los embeddings del fichero
            except Exception:
                log.warning("No se pudo compactar la base de datos tras el borrado")
            log.warning("Todos los perfiles biométricos han sido eliminados (%d)", n)
            return n
