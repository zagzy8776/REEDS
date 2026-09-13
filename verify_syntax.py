import ast
import sys

files = [
    "backend/app/services/market_metrics.py",
    "backend/app/services/predictions.py",
    "backend/app/services/market_gate.py",
    "backend/app/services/prediction_learning.py",
    "backend/app/services/model_registry.py",
    "backend/app/services/model_bootstrap.py",
    "backend/app/services/runtime_hardening.py",
    "backend/app/api/model_sync.py",
    "backend/app/api/admin.py",
    "backend/app/ml/ensemble.py",
    "backend/app/ml/basketball.py",
    "backend/app/ml/calibration.py",
    "backend/app/ml/model_cache.py",
    "backend/app/db/session.py",
    "backend/app/db/models.py",
    "backend/app/main.py",
    "backend/app/services/fixture_prediction.py",
    "backend/app/services/scheduler.py",
]

ok = True
for path in files:
    try:
        with open(path, encoding="utf-8") as handle:
            ast.parse(handle.read())
        print(f"OK  {path}")
    except Exception as exc:
        ok = False
        print(f"FAIL {path}: {exc}")

sys.exit(0 if ok else 1)