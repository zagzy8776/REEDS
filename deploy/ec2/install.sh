#!/usr/bin/env bash
# REEDS EC2 worker installer.
# - REQUIRES Python 3.11 (refuses to build the venv with any other version).
# - Creates a dedicated venv OUTSIDE the repo.
# - Installs backend/requirements.txt (pins scikit-learn==1.6.0, matching Render).
# - Creates the model + log directories.
# - Does NOT start or schedule any training.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${REEDS_VENV_DIR:-$HOME/reeds-venv}"
LOG_DIR="$REPO_ROOT/deploy/ec2/logs"

PYTHON_BIN=""
if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.11)"
fi
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.11 is REQUIRED for the REEDS training worker." >&2
    echo "The venv was NOT created — refusing to silently use another Python version." >&2
    echo "" >&2
    echo "Install Python 3.11 from the distro archives (no external PPA needed):" >&2
    echo "  sudo apt-get update" >&2
    echo "  sudo apt-get install -y python3.11 python3.11-venv python3.11-dev" >&2
    echo "Note: on Ubuntu 24.04 python3.11 is in the 'universe' archive (default repos);" >&2
    echo "      python3.12 is the app default there, so python3.11 must be installed explicitly." >&2
    exit 2
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PY_VERSION" != "3.11" ]; then
    echo "ERROR: expected Python 3.11 but '$PYTHON_BIN' reports $PY_VERSION." >&2
    exit 2
fi

echo "== REEDS EC2 worker install =="
echo "repo:      $REPO_ROOT"
echo "python:    $PYTHON_BIN (Python $PY_VERSION)"
echo "venv:      $VENV_DIR"

mkdir -p "$VENV_DIR" "$LOG_DIR" "$REPO_ROOT/backend/data/models"
"$PYTHON_BIN" -m venv "$VENV_DIR" || {
    echo "ERROR: 'python3.11 -m venv' failed. Ensure python3.11-venv is installed:" >&2
    echo "  sudo apt-get install -y python3.11-venv" >&2
    exit 2
}
VENV_PY_VERSION="$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$VENV_PY_VERSION" != "3.11" ]; then
    echo "ERROR: venv resolved to Python $VENV_PY_VERSION; expected 3.11." >&2
    exit 2
fi
"$VENV_DIR/bin/pip" install --upgrade pip wheel setuptools
"$VENV_DIR/bin/pip" install -r "$REPO_ROOT/backend/requirements.txt"

echo "== verifying scikit-learn lock =="
"$VENV_DIR/bin/python" - <<'PY'
import sklearn
major, minor = (int(p) for p in sklearn.__version__.split(".")[:2])
assert (1, 6) <= (major, minor) < (1, 7), f"sklearn {sklearn.__version__} out of 1.6.x lock"
print(f"scikit-learn {sklearn.__version__} OK (lock range 1.6.x)")
PY

echo "== install complete =="
echo "Next (manual only):"
echo "  1. export DATABASE_URL, ADMIN_API_KEY, RENDER_URL (see deploy/ec2/env.example)"
echo "  2. dry-run:  $VENV_DIR/bin/python $REPO_ROOT/backend/scripts/ec2_train_worker.py validate"
echo "  3. one sport: $VENV_DIR/bin/python $REPO_ROOT/backend/scripts/ec2_train_worker.py train --sport soccer"
echo "  4. full run: $VENV_DIR/bin/python $REPO_ROOT/backend/scripts/ec2_train_worker.py run-all"