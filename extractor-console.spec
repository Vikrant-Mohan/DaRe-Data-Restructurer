# PyInstaller spec for the Extractor Console desktop app (Windows).
# Build:  .venv/Scripts/pyinstaller extractor-console.spec --noconfirm
# Output: dist/extractor-console/ExtractorConsole.exe  (onedir: fast startup)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
ROOT = Path(SPECPATH)
UI = ROOT / "ui"

# litellm reads model-cost JSON files from its package dir at import time;
# without these the frozen app crashes on startup.
DATA_FILES = collect_data_files("litellm")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "extractor.api.main",
    "extractor.api.restructure",
    "extractor.api.web",
    "extractor.excel",
    "extractor.tabular",
    # LLM backends import lazily; pull the common ones in explicitly
    "instructor",
    "litellm",
    "openpyxl",
    "anyio._backends._asyncio",
]

a = Analysis(
    ["desktop.py"],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=DATA_FILES + ([(str(UI), "ui")] if UI.is_dir() else []),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ExtractorConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a console window: it hosts the "close to quit" loop
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="extractor-console",
)
