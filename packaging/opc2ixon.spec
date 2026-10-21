# PyInstaller spec – used by build_mac.sh and build_windows.bat (do not run directly from another folder)
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)
sys.path.insert(0, ROOT)
from opc2ixon import APP_NAME, __version__  # noqa: E402

DIST_NAME = "opc2ixon"

datas = []
for name in ("NOTICE.txt", "THIRD_PARTY_LICENSES.txt"):
    f = os.path.join(SPECPATH, name)  # noqa: F821
    if os.path.exists(f):
        datas.append((f, "."))

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "pytest", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=DIST_NAME,
    console=False,  # windowed application
    upx=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name=DIST_NAME, upx=False)  # noqa: F821

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier="io.github.opc2ixon",
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
