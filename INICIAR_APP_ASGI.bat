@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "UVICORN_MODULE=uvicorn"

if not exist "%PYTHON_EXE%" (
    echo [INFO] Creando entorno virtual en %VENV_DIR%...
    py -3 -m venv "%VENV_DIR%"
    if errorlevel 1 goto :error
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :error

echo [INFO] Actualizando pip...
"%PYTHON_EXE%" -m pip install --upgrade pip >NUL
if errorlevel 1 goto :error

echo [INFO] Instalando dependencias requeridas...
"%PYTHON_EXE%" -m pip install --upgrade "flask[async]" "uvicorn[standard]" requests beautifulsoup4 lxml pandas selenium >NUL
if errorlevel 1 goto :error

if errorlevel 1 goto :error

set "HOST=0.0.0.0"
set "PORT=5000"

set "WORKERS=%NUMBER_OF_PROCESSORS%"
if "%WORKERS%"=="" set "WORKERS=2"

echo.
echo ======================================================
echo  Iniciando servidor ASGI con Uvicorn

echo  URL: http://localhost:%PORT%
echo  Workers: %WORKERS%
echo ======================================================
echo.

"%PYTHON_EXE%" -m %UVICORN_MODULE% app:asgi_app --host %HOST% --port %PORT% --workers %WORKERS% --lifespan off
if errorlevel 1 goto :error

echo.
echo Servidor finalizado.
pause
exit /b 0

:error
echo.
echo [ERROR] El script se ha detenido por un fallo. Revisa los mensajes anteriores.
pause
exit /b 1
