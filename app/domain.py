"""Tipos compartidos entre visión, seguimiento y eventos (sin dependencias pesadas)."""
from __future__ import annotations

import enum
from dataclasses import dataclass


class IdentityStatus(str, enum.Enum):
    AUTHORIZED = "AUTHORIZED"  # persona reconocida y autorizada
    UNKNOWN = "UNKNOWN"  # rostro con calidad suficiente que no coincide con ningún perfil
    UNVERIFIED = "UNVERIFIED"  # presencia humana sin información facial suficiente


STATUS_LABEL_ES = {
    IdentityStatus.AUTHORIZED: "autorizada",
    IdentityStatus.UNKNOWN: "desconocida",
    IdentityStatus.UNVERIFIED: "no verificable",
}


@dataclass(frozen=True)
class Observation:
    """Una persona vista en un ciclo de análisis.

    `face_status` es None cuando no se pudo evaluar ningún rostro (persona sin cara visible).
    """

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 en píxeles
    person_confidence: float
    face_status: IdentityStatus | None = None
    name: str | None = None
    similarity: float | None = None
    reasons: tuple[str, ...] = ()
