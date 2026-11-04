@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Chua cai dat. Chay setup.bat truoc. & pause & exit /b 1)
start "" http://127.0.0.1:8090
.venv\Scripts\python -m app.main
pause
