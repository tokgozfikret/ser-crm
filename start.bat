@echo off
REM ============================================================
REM  SER-CRM baslatma dosyasi (Windows)
REM  Cift tiklayarak veya Gorev Zamanlayici ile calistirilabilir.
REM ============================================================
cd /d "%~dp0"

REM Sanal ortam varsa onu kullan, yoksa sistem Python'u
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo SER-CRM baslatiliyor...
"%PY%" serve.py

echo.
echo Sunucu durdu. Kapatmak icin bir tusa basin.
pause
