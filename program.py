"""Single-file launcher para Visual Studio / IDE debug.

Reemplaza el flujo de start.cmd. Arranca STT + TTS + HTTP client
en threads/subprocesses con manejo de señales limpio.

Uso:
    python program.py              # arranca todo y espera Ctrl-C
    python program.py --stt-only   # solo STT (debug)
    python program.py --no-http    # sin servidor de archivos para el cliente

Pensado para F5 / Run en VS Code / PyCharm.
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PYTHON = sys.executable


def spawn(name: str, args: list[str], log: Path) -> subprocess.Popen:
    """Lanza un proceso hijo con logs redirigidos a archivo. Devuelve Popen."""
    log.parent.mkdir(exist_ok=True)
    f = open(log, "ab", buffering=0)
    proc = subprocess.Popen(
        [PYTHON, *args],
        cwd=ROOT,
        stdout=f,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"[{name}] started PID={proc.pid} -> {log}", flush=True)
    return proc


def main() -> int:
    p = argparse.ArgumentParser(description="Kokoro TTS stack launcher")
    p.add_argument("--stt-only", action="store_true", help="Solo servidor STT")
    p.add_argument("--tts-only", action="store_true", help="Solo servidor TTS")
    p.add_argument("--no-http", action="store_true", help="Sin servidor de archivos del cliente")
    p.add_argument("--host", default=os.getenv("BIND_HOST", "127.0.0.1"),
                   help="Interfaz de escucha (0.0.0.0 para exponer en la LAN)")
    p.add_argument("--stt-port", type=int, default=8100)
    p.add_argument("--tts-port", type=int, default=8990)
    p.add_argument("--http-port", type=int, default=5500)
    args = p.parse_args()

    procs: list[tuple[str, subprocess.Popen, Path]] = []
    logs = ROOT / "logs"

    def start_stt():
        f = logs / "stt_server.log"
        proc = spawn(
            "STT",
            ["-m", "uvicorn", "server.stt.stt_server:app",
             "--host", args.host, "--port", str(args.stt_port), "--log-level", "info"],
            f,
        )
        procs.append(("STT", proc, f))

    def start_tts():
        f = logs / "tts_server.log"
        proc = spawn(
            "TTS",
            ["-m", "uvicorn", "server.tts.websocket_server:app",
             "--host", args.host, "--port", str(args.tts_port), "--log-level", "info"],
            f,
        )
        procs.append(("TTS", proc, f))

    def start_http():
        f = logs / "http_client.log"
        proc = spawn(
            "HTTP",
            ["serve_nocache.py", str(args.http_port), args.host],
            f,
        )
        procs.append(("HTTP", proc, f))

    if args.stt_only:
        start_stt()
    elif args.tts_only:
        start_tts()
    else:
        start_stt()
        time.sleep(2)
        start_tts()
        time.sleep(1)
        if not args.no_http:
            start_http()

    print("\n[main] All services launched. Press Ctrl-C to stop.\n", flush=True)
    print(f"  Cliente:        http://localhost:{args.http_port}/client/client_example.html")
    print(f"  STT health:     http://localhost:{args.stt_port}/health")
    print(f"  TTS health:     http://localhost:{args.tts_port}/health")
    print(f"  Logs:           {logs}\n")

    def shutdown(*_):
        print("\n[main] Stopping...", flush=True)
        for name, proc, _ in reversed(procs):
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass
            print(f"  {name} stopped", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, shutdown)

    # Loop until any process dies.
    while True:
        for name, proc, log in procs:
            ret = proc.poll()
            if ret is not None:
                print(f"[main] {name} exited with code {ret}. Tailing {log}...", flush=True)
                with open(log, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-2000:]
                print(tail, flush=True)
                shutdown()
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
