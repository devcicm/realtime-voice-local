"""Mini static server for the client that disables HTTP caching.

The default `python -m http.server` sends `Last-Modified` and allows
conditional requests, but browsers may still cache aggressively. This
server always sends `Cache-Control: no-cache, no-store, must-revalidate`
and neutralizes conditional requests (If-Modified-Since / If-None-Match)
so the browser ALWAYS gets a full 200 + body, never a 304 or a cached
copy. This is what guarantees client_example.html edits reach the tab.
"""
import http.server
import socketserver
import os
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
HOST = sys.argv[2] if len(sys.argv) > 2 else os.getenv("BIND_HOST", "127.0.0.1")

# Fecha antigua: cualquier If-Modified-Since que llegue será siempre
# MENOR que el mtime real del archivo, así send_head() nunca responde 304.
EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"
# ETag que nunca coincide (usamos mtime real), para que If-None-Match
# tampoco produzca jamás un 304.
NEVER_MATCH_TAG = '"never-match-nowebcache"'


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # Always send no-cache headers so the browser never serves stale JS.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        try:
            full = self.translate_path(self.path)
            if os.path.isfile(full):
                mtime = int(os.path.getmtime(full) * 1000)
                self.send_header("ETag", f'"{mtime}"')
                self.send_header("Last-Modified", self.date_time_string(mtime / 1000))
        except Exception:
            pass
        super().end_headers()

    def _neutralize_conditional(self):
        # Silence any conditional headers a browser/proxy may still send.
        if self.headers.get("If-Modified-Since"):
            self.headers.replace_header("If-Modified-Since", EPOCH)
        if self.headers.get("If-None-Match"):
            self.headers.replace_header("If-None-Match", NEVER_MATCH_TAG)

    def _is_hidden(self) -> bool:
        # Never serve dotfiles/dotdirs (.env, .git, ...): they hold secrets.
        path = unquote(self.path.split("?", 1)[0].split("#", 1)[0]).replace("\\", "/")
        return any(part.startswith(".") for part in path.split("/") if part)

    def do_GET(self):
        if self._is_hidden():
            self.send_error(404)
            return
        self._neutralize_conditional()
        super().do_GET()

    def do_HEAD(self):
        if self._is_hidden():
            self.send_error(404)
            return
        self._neutralize_conditional()
        super().do_HEAD()


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    os.chdir(ROOT)
    server = ThreadedHTTPServer((HOST, PORT), NoCacheHandler)
    print(f"[http-no-cache] serving {ROOT} on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass