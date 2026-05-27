# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the VideoMatrix backend.

Run with:
    pyinstaller backend/videomatrix-backend.spec

Outputs a self-contained executable under `dist/` that bundles:
- the FastAPI / uvicorn server (no system Python required)
- ffmpeg.exe / ffprobe.exe (or the Mac equivalents) sitting next to it,
  resolved at runtime via _MEIPASS by `app/core/ffmpeg.py:_find_tool`.

The CI workflow downloads platform-appropriate ffmpeg static builds into
`backend/ffmpeg/` before invoking this spec; locally you can drop binaries
in the same folder for a Mac/Linux dev build.
"""

import os
import sys
from pathlib import Path

block_cipher = None

BACKEND_DIR = Path(os.path.abspath(SPECPATH))
FFMPEG_DIR = BACKEND_DIR / 'ffmpeg'

# Collect ffmpeg / ffprobe regardless of which platform we are building on.
# Each entry is (source_path_on_disk, target_subdir_inside_bundle).
binaries = []
for name in ('ffmpeg', 'ffmpeg.exe', 'ffprobe', 'ffprobe.exe'):
    candidate = FFMPEG_DIR / name
    if candidate.exists():
        binaries.append((str(candidate), '.'))

# Hidden imports — uvicorn dynamically loads these and PyInstaller's static
# analysis would otherwise miss them.
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'fastapi',
    'pydantic',
    'pydantic.deprecated.decorator',
    'aiofiles',
    'app.api.routes',
    'app.core.ffmpeg',
    'app.core.video_matrix',
    'app.services.task_service',
    'app.models.schemas',
]

a = Analysis(
    ['app/main.py'],
    pathex=[str(BACKEND_DIR)],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='videomatrix-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
