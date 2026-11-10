"""Autenticación por API key para exponer el servicio fuera de la red local.

Desactivada por defecto (uso local sin cambios). Se activa con la variable de
entorno API_KEYS (una o varias claves separadas por comas):

    API_KEYS=clave-larga-1,clave-larga-2

Con claves configuradas, toda petición HTTP y WebSocket debe traer una válida en:
  * cabecera  Authorization: Bearer <clave>      (recomendado; SDK de OpenAI)
  * cabecera  X-API-Key: <clave>
  * query     ?api_key=<clave>                   (WebSocket desde navegador, que
                                                  no puede poner cabeceras; queda
                                                  en logs de proxies: úsalo con wss)

Exentos: GET /health (monitorización) y las peticiones OPTIONS (preflight CORS).
Sin clave válida: HTTP 401 / WebSocket rechazado en el handshake (403).

Generar una clave:  python -c "import secrets; print(secrets.token_urlsafe(32))"
"""
import hmac
import json
import logging
import os
from typing import Iterable, Optional
from urllib.parse import parse_qs

try:  # .env opcional
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset({"/health"})


def api_keys_from_env() -> list[str]:
    return [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]


def _extract_key(scope) -> Optional[str]:
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    if headers.get("x-api-key"):
        return headers["x-api-key"].strip()
    qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    if qs.get("api_key"):
        return qs["api_key"][0]
    return None


class APIKeyMiddleware:
    """Middleware ASGI puro: cubre HTTP y WebSocket (los BaseHTTPMiddleware no
    ven los WebSocket). Si no hay claves, deja pasar todo."""

    def __init__(self, app, keys: Optional[Iterable[str]] = None):
        self.app = app
        self.keys = [k.encode() for k in (api_keys_from_env() if keys is None else keys)]
        if self.keys:
            logger.info("Autenticación por API key activa (%d clave/s)", len(self.keys))

    def _valid(self, key: Optional[str]) -> bool:
        if not key:
            return False
        k = key.encode()
        # compare_digest contra todas: tiempo constante, sin cortocircuito.
        return any([hmac.compare_digest(k, ok) for ok in self.keys])

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if (not self.keys or kind not in ("http", "websocket")
                or scope.get("path") in PUBLIC_PATHS
                or (kind == "http" and scope.get("method") == "OPTIONS")):
            return await self.app(scope, receive, send)

        if self._valid(_extract_key(scope)):
            return await self.app(scope, receive, send)

        client = (scope.get("client") or ("?",))[0]
        logger.warning("API key ausente o inválida: %s %s desde %s", kind, scope.get("path"), client)
        if kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": "API key requerida"})
            return
        body = json.dumps({"detail": "API key ausente o inválida"}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="tts"'),
        ]})
        await send({"type": "http.response.body", "body": body})
