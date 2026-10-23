@echo off
rem Inicia o Verificador de Talhoes. Na primeira vez cria o ambiente e instala as dependencias.
rem Para usar outra porta: iniciar.bat 80
cd /d "%~dp0"
set PORTA=%1
if "%PORTA%"=="" set PORTA=8080

if not exist ".venv\Scripts\python.exe" (
    echo Preparando o ambiente Python pela primeira vez...
    py -3 -m venv .venv || python -m venv .venv
    if errorlevel 1 (
        echo Nao achei o Python. Instale em https://www.python.org/downloads/ e rode de novo.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)
rem sempre confere as dependencias: quando o projeto ganha uma nova, ela e instalada aqui
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt

".venv\Scripts\python.exe" app.py --porta %PORTA%
pause
