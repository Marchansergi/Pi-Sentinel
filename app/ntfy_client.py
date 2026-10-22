"""Cliente HTTP de ntfy (https://docs.ntfy.sh/publish/).

Publica con JSON en la raíz del servidor: POST {server}/ con {"topic","title","message","priority","tags"}.
Así el título puede llevar emojis/UTF-8 sin codificar cabeceras. Si hay token se envía como
`Authorization: Bearer <token>`.

El cliente NUNCA guarda ni registra el token o el tópico y clasifica cada fallo como reintentable o no.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 425, 429}


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status_code: int | None = None
    retryable: bool = False
    kind: str = "ok"  # ok | auth | http | rate_limit | connection | timeout | config
    error: str = ""
    retry_after: float | None = None


def _clean(text: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


class NtfyClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # inyectable en pruebas

    def publish(
        self,
        server: str,
        topic: str,
        title: str,
        message: str,
        priority: int = 4,
        token: str | None = None,
        timeout: float = 10.0,
        tags: list[str] | None = None,
    ) -> SendResult:
        if not server or not topic:
            return SendResult(False, None, False, "config", "Falta el servidor o el tópico de ntfy")
        headers = {"Content-Type": "application/json", "User-Agent": "pi-sentinel"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"topic": topic, "title": title, "message": message, "priority": int(priority)}
        if tags:
            body["tags"] = tags
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout), transport=self._transport, follow_redirects=False) as c:
                resp = c.post(server.rstrip("/") + "/", json=body, headers=headers)
        except httpx.TimeoutException:
            return SendResult(False, None, True, "timeout", f"Tiempo de espera agotado ({timeout:.0f}s)")
        except (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError) as exc:
            return SendResult(False, None, True, "connection", f"Sin conexión con el servidor ({exc.__class__.__name__})")
        except httpx.InvalidURL:
            return SendResult(False, None, False, "config", "URL del servidor no válida")
        except httpx.HTTPError as exc:
            return SendResult(False, None, True, "connection", f"Error de red ({exc.__class__.__name__})")
        return self._classify(resp)

    @staticmethod
    def _classify(resp: httpx.Response) -> SendResult:
        code = resp.status_code
        if 200 <= code < 300:
            return SendResult(True, code, False, "ok", "")
        detail = ""
        try:
            data = resp.json()
            if isinstance(data, dict):
                detail = _clean(str(data.get("error", "")))
        except ValueError:
            pass
        suffix = f": {detail}" if detail else ""
        if code in (401, 403):
            return SendResult(False, code, False, "auth",
                              f"Autenticación rechazada (HTTP {code}). Revisa el token o los permisos del tópico{suffix}")
        if code == 429:
            ra = None
            try:
                ra = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                pass
            return SendResult(False, code, True, "rate_limit", f"Límite de peticiones del servidor (HTTP 429){suffix}", ra)
        if code in _RETRYABLE_STATUS or code >= 500:
            return SendResult(False, code, True, "http", f"Error temporal del servidor (HTTP {code}){suffix}")
        if 300 <= code < 400:
            return SendResult(False, code, False, "config", f"El servidor redirige (HTTP {code}); revisa la URL")
        return SendResult(False, code, False, "http", f"Petición rechazada (HTTP {code}){suffix}")
