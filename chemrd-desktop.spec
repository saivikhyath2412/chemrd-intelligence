# PyInstaller build recipe for the double-clickable local ChemR&D app.
from PyInstaller.utils.hooks import collect_submodules, collect_all
rdkit_datas, rdkit_binaries, rdkit_imports = collect_all('rdkit')

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("webview")
    + collect_submodules("openai")
    + rdkit_imports
)

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["."],
    binaries=rdkit_binaries,
    datas=[("frontend", "frontend"), *rdkit_datas],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ChemRD-Intelligence",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
