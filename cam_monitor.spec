# cam_monitor.spec
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Dados extras a incluir no bundle
added_files = [
    ("config.yaml", "."),
    ("cam", "cam"),
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=added_files,
    hiddenimports=[
        "cam.analyzer",
        "cam.audio_recorder",
        "cam.action_engine",
        "cam.capture",
        "cam.db",
        "cam.detector",
        "cam.gui",
        "cam.service",
        "cam.transcriber",
        "cam.transcribe_worker",
        "PIL._tkinter_finder",
        "tkinter",
        "tkinter.ttk",
        "cv2",
        "numpy",
        "httpx",
        "sounddevice",
        "yaml",
        "telegram",
        "faster_whisper",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CAM Monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # sem janela de console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CAM Monitor",
)
