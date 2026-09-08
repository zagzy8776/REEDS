$ErrorActionPreference = "Continue"
cd e:\loyallllllll\REEDS-git
& ".\.venv\Scripts\python.exe" -m pip install --no-warn-script-location `
    "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "SQLAlchemy==2.0.36" `
    "psycopg[binary]==3.2.3" "pydantic-settings==2.7.1" "python-dotenv==1.0.1" `
    "pandas==2.2.3" "numpy==1.26.4" "scikit-learn==1.6.0" "joblib==1.4.2" `
    "scipy==1.14.1" "requests==2.32.3" "beautifulsoup4==4.12.3" "APScheduler==3.11.0" `
    "python-multipart==0.0.20" "alembic==1.14.0" "pytest" "pytest-asyncio" 2>&1 | Out-File -FilePath pip_install.log -Encoding utf8
Write-Output "DONE" | Out-File -FilePath pip_install_done.txt -Encoding utf8