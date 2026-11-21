@echo off
REM Build a single-file MoreThanTinyTask.exe into the dist\ folder
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean MoreThanTinyTask.spec
echo.
echo Done: dist\MoreThanTinyTask.exe
pause
