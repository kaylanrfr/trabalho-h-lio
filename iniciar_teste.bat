@echo off
title Inicializador de NNos Python

echo ======================================
echo   Inicializando instancias do sistema
echo ======================================
echo.

set PYTHON=python


set SCRIPT=no.py

echo [1/4] Iniciando Coordenador...
start "Coordenador" powershell -NoExit -Command "%PYTHON% %SCRIPT% --port 10001 --name Coordenador"
timeout /t 7 >nul

REM Inicia os demais nós
echo [2/4] Iniciando Nodo2...
start "Nodo2" powershell -NoExit -Command "%PYTHON% %SCRIPT% --port 10002 --name Nodo2"
timeout /t 3 >nul

echo [3/4] Iniciando Nodo3...
start "Nodo3" powershell -NoExit -Command "%PYTHON% %SCRIPT% --port 10003 --name Nodo3"
timeout /t 3 >nul

echo [4/4] Iniciando Nodo4...
start "Nodo4" powershell -NoExit -Command "%PYTHON% %SCRIPT% --port 10004 --name Nodo4"

echo.
echo ======================================
echo Todas as instâncias foram iniciadas!
echo ======================================
pause
