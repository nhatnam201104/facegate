@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
echo [1/3] Tao moi truong ao .venv ...
if not exist .venv %PY% -m venv .venv || goto :err
echo [2/3] Cai thu vien ...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || goto :err
echo [3/3] Tai model ...
.venv\Scripts\python scripts\download_models.py %* || goto :err
echo.
echo Cai dat xong. Chay run.bat de khoi dong.
pause
exit /b 0
:err
echo.
echo Co loi xay ra, xem thong bao phia tren.
pause
exit /b 1
