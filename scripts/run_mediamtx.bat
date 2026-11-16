@echo off
REM Khoi chay MediaMTX kem thu muc dem cho FaceGate v4 (Windows dev)
set ROOT_DIR=%~dp0..
if "%FACEGATE_RING_DIR%"=="" set FACEGATE_RING_DIR=%TEMP%\facegate_ring
if "%CAM_USER%"=="" set CAM_USER=admin
if "%CAM_PASS%"=="" set CAM_PASS=changeme
if "%CAM_IP%"=="" set CAM_IP=192.168.50.10

if not exist "%FACEGATE_RING_DIR%" mkdir "%FACEGATE_RING_DIR%"
if "%MEDIAMTX_BIN%"=="" set MEDIAMTX_BIN=mediamtx.exe
"%MEDIAMTX_BIN%" "%ROOT_DIR%\deploy\mediamtx.yml"
