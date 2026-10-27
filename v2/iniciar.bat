@echo off
rem Verificador de Talhoes v2. Na primeira vez cria o ambiente Python e compila a tela.
rem Para usar outra porta: iniciar.bat 80
cd /d "%~dp0"
set PORTA=%1
if "%PORTA%"=="" set PORTA=8000

if not exist "backend\.venv\Scripts\python.exe" (
    echo Preparando o ambiente Python pela primeira vez...
    py -3 -m venv backend\.venv || python -m venv backend\.venv
    if errorlevel 1 (
        echo Nao achei o Python 3.11+. Instale em https://www.python.org/downloads/ e rode de novo.
        pause
        exit /b 1
    )
)
"backend\.venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt

if not exist "frontend\dist\index.html" (
    echo Compilando a tela, precisa do Node.js...
    pushd frontend
    call npm install --no-audit --no-fund && call npm run build
    popd
)

echo Verificador de Talhoes v2 em http://localhost:%PORTA%  (Ctrl+C para parar)
"backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port %PORTA%
pause
