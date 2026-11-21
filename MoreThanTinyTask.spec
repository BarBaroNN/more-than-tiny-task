# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm --clean MoreThanTinyTask.spec

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["pynput.keyboard._win32", "pynput.mouse._win32"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf",
              "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MoreThanTinyTask",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
