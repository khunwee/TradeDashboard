@echo off
echo ============================================================
echo  Trading Dashboard - Windows Install (No version pinning)
echo ============================================================
echo.

echo [1/3] Upgrading pip...
python -m pip install --upgrade pip
echo.

echo [2/3] Installing all packages (latest compatible versions)...
pip install fastapi "uvicorn[standard]" sqlalchemy psycopg2-binary alembic "python-jose[cryptography]" "passlib[bcrypt]" "numpy<2.0" pandas apscheduler httpx python-multipart pydantic pydantic-settings python-dotenv reportlab Pillow websockets aiofiles jinja2 email-validator pyotp user-agents slowapi
echo.

echo [3/3] Verifying key packages...
python -c "import fastapi; print('  fastapi     OK', fastapi.__version__)"
python -c "import sqlalchemy; print('  sqlalchemy  OK', sqlalchemy.__version__)"
python -c "import uvicorn; print('  uvicorn     OK')"
python -c "import slowapi; print('  slowapi     OK')"
python -c "import psycopg2; print('  psycopg2   OK', psycopg2.__version__)"
python -c "import pydantic; print('  pydantic    OK', pydantic.__version__)"
python -c "import pandas; print('  pandas      OK', pandas.__version__)"
echo.

echo ============================================================
echo  All done! Now run:   python main.py
echo ============================================================
pause
