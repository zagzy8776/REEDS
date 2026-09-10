"""Kaggle P0 environment + run diagnostic (self-contained, no repo clone needed locally).

Fetched and executed by a tiny launcher cell:
    python3 -c "import urllib.request;open('/kaggle/working/ck.py','w').write(urllib.request.urlopen('<raw URL>').read());exec(open('/kaggle/working/ck.py').read())"

Prints environment facts, then runs the real P0 worker with full stdout/stderr capture.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request

print("python:", sys.executable, "| version:", sys.version.split()[0], flush=True)

try:
    from kaggle_secrets import UserSecretsClient
    url = UserSecretsClient().get_secret("DATABASE_URL").strip()
    print("kaggle_secrets: OK | DATABASE_URL loaded | starts_with_postgres:",
          url.startswith("postgres"), flush=True)
    os.environ["DATABASE_URL"] = url
except Exception as exc:  # noqa: BLE001
    print("kaggle_secrets ERROR:", exc, flush=True)

try:
    winget = subprocess.run([sys.executable, "-c",
                             "import urllib.request,hashlib,re;"
                             "u=os.environ.get('DATABASE_URL','');"
                             "h=re.search(r'@([^/:]+)',u);"
                             "print('host_fp:', hashlib.sha256(h.group(1).encode()).hexdigest()[:16])"],
                            capture_output=True, text=True, env=os.environ)
    print("host fingerprint check:", winget.stdout.strip(), winget.stderr.strip(), flush=True)
except Exception as exc:  # noqa: BLE001
    print("fingerprint check ERROR:", exc, flush=True)

# 1) Clone the exact branch/commit
SHA = "507314b4b27489156b4773aabf7ba028ec5a9105"
wk = "/kaggle/working/REEDS"
if os.path.exists(wk):
    os.system("rm -rf " + wk)
c = subprocess.run(["git", "clone", "-b", "p0-backfill",
                    "https://github.com/zagzy8776/REEDS.git", wk], capture_output=True, text=True)
print("clone returncode:", c.returncode, "| stdout:", c.stdout[-200:], "| stderr:", c.stderr[-200:], flush=True)

# 2) Run the real P0 worker with every byte captured
worker = ("https://raw.githubusercontent.com/zagzy8776/REEDS/" + SHA + "/kaggle/run_p0_backfill.py")
p0 = "/kaggle/working/p0.py"
try:
    p0src = urllib.request.urlopen(worker).read()
    open(p0, "wb").write(p0src)
    print("worker downloaded:", len(p0src), "bytes", flush=True)
except Exception as exc:  # noqa: BLE001
    print("worker download ERROR:", exc, flush=True)
    p0src = b""

if p0src:
    r = subprocess.run([sys.executable, p0], capture_output=True, text=True, timeout=3600,
                       env=os.environ, cwd="/kaggle/working")
    print("P0 exit code:", r.returncode, flush=True)
    print("=== STDOUT (last 8000 chars) ===", flush=True)
    print(r.stdout[-8000:], flush=True)
    print("=== STDERR (last 4000 chars) ===", flush=True)
    print(r.stderr[-4000:], flush=True)