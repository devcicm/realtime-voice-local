"""Network exposure defaults shared by the STT and TTS servers.

By default the stack is local-only:
  * servers bind to 127.0.0.1 (override with BIND_HOST=0.0.0.0)
  * CORS only accepts localhost origins plus "null" (client opened via file://)

To expose the stack on the LAN set, e.g.:
  BIND_HOST=0.0.0.0
  CORS_ORIGINS=http://192.168.1.50:5500,http://mi-pc:5500   (or "*")

This module reads os.environ directly (no pydantic Settings) so the STT
server can use it without pulling in the TTS configuration.
"""
import os

try:  # .env is optional; the STT server may run without python-dotenv
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_BIND_HOST = "127.0.0.1"

# localhost / 127.0.0.1 / [::1] on any port, http or https.
LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"


def bind_host() -> str:
    return os.getenv("BIND_HOST", DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST


def cors_kwargs() -> dict:
    """Keyword arguments for starlette's CORSMiddleware."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    common = {"allow_methods": ["*"], "allow_headers": ["*"]}
    if raw == "*":
        # Wildcard origin cannot be combined with credentials.
        return {"allow_origins": ["*"], "allow_credentials": False, **common}
    extra = [o.strip() for o in raw.split(",") if o.strip()]
    return {
        "allow_origins": ["null", *extra],
        "allow_origin_regex": LOCAL_ORIGIN_REGEX,
        "allow_credentials": True,
        **common,
    }
