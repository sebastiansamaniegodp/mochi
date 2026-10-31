@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
py -3 -c "import sys,struct; assert sys.version_info >= (3,10) and struct.calcsize('P')==8" >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  python -c "import sys,struct; assert sys.version_info >= (3,10) and struct.calcsize('P')==8" >nul 2>nul
  if errorlevel 1 goto missing_python
  python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto setup_error
:dependencies
".venv\Scripts\python.exe" -c "import vosk,serial,tkinter,numpy,soundcard,pyaudiowpatch" >nul 2>nul
if not errorlevel 1 goto launch
echo Preparando Mochi. La primera vez requiere Internet...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_error
:launch
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto setup_error
exit /b 0
:missing_python
echo Instala Python de 64 bits x64, version 3.10 o superior, desde https://www.python.org/downloads/windows/
echo Incluye pip y Tkinter. En el instalador clasico, activa Add Python to PATH.
echo Luego abre este archivo otra vez.
pause
exit /b 1
:setup_error
echo Mochi no pudo iniciar. Copia el error que aparece arriba para revisarlo.
pause
exit /b 1
