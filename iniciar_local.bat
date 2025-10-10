
@echo off
TITLE Probador Local de App

echo --------------------------------------------------------
echo       INICIADOR DE APLICACION LOCAL
echo --------------------------------------------------------
echo.

echo [PASO 1 de 2] Ejecutando el scraper para crear/actualizar data.json...
echo Este proceso puede tardar uno o dos minutos. Por favor, espera.
echo.

REM Ejecuta el script de scraping
py run_scraper.py

REM Comprueba si el scraper dio un error. Si el errorlevel no es 0, hubo un problema.
IF %errorlevel% NEQ 0 (
    echo.
    echo ***********************************************************
    echo *  ERROR: El script de scraping ha fallado.                *
    echo *  La aplicacion web no se puede iniciar.                 *
    echo *  Revisa los mensajes de error en esta ventana.          *
    echo ***********************************************************
    echo.
    pause
    exit /b %errorlevel%
)

echo.
echo [PASO 2 de 2] Scraper finalizado con exito.
echo Iniciando el servidor web de Flask...
echo.
echo >> Tu aplicacion estara disponible en: http://127.0.0.1:8080
echo >> Manten esta ventana abierta para que el servidor funcione.
echo >> Cierra la ventana para detener el servidor.
echo.

REM Si el scraper funciono, inicia la app
py app.py
