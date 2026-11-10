"""Sanity check: parse the monolith client's inline JS with `node --check`."""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HTML = Path(__file__).resolve().parent / "client" / "client_example.html"

content = HTML.read_text(encoding="utf-8")
m = re.search(r"<script[^>]*>(.*?)</script>", content, re.DOTALL)
if not m:
    print("No script tag found")
    sys.exit(1)
js = m.group(1)
print(f"Script length: {len(js)} chars, {js.count(chr(10))} lines")

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "check.js"
    path.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)

print("STDOUT:", r.stdout[:500])
print("STDERR:", r.stderr[:1500])
print("returncode:", r.returncode)
sys.exit(r.returncode)
