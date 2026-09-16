@echo off
REM Install the full high-quality ML ensemble (xgboost is the big wheel; detached so it survives the shell).
cd /d E:\REEDS-main\REEDS-main
python -m pip install --no-input xgboost lightgbm optuna > E:\REEDS-main\REEDS-main\pip_install.log 2>&1
echo DONE > E:\REEDS-main\REEDS-main\pip_done.flag
