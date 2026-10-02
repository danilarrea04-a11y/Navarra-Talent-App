@echo off
REM Instala el entorno de la app KWD en este equipo (solo hace falta la primera vez).
cd /d %~dp0

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -c "import streamlit, highspy, pulp" >nul 2>nul && goto ok
    echo El entorno .venv viene de otro equipo o esta incompleto: se vuelve a crear.
    rmdir /s /q .venv
)

echo Creando entorno virtual...
%PY% -m venv .venv || goto error
echo Instalando dependencias (tarda unos minutos)...
.venv\Scripts\python.exe -m pip install --upgrade pip || goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto error

:ok
echo.
echo Instalacion correcta. Para abrir la app haz doble clic en iniciar_dashboard.bat
pause
exit /b 0

:error
echo.
echo ERROR en la instalacion. Comprueba que Python 3.14 esta instalado (python.org,
echo marcando "Add python.exe to PATH") y que hay conexion a internet.
pause
exit /b 1
