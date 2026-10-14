# Curated Windows onedir build used for local validation.
# The GitHub release workflow has its own pinned cross-platform build commands.
import os

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = os.path.abspath(SPECPATH)
APP = os.path.join(ROOT, "dictado", "src", "instant2_app")

datas = [
    (os.path.join(APP, "qml", "overlay_web_host.qml"), "instant2_app/qml"),
    (os.path.join(APP, "qml", "panel_web_host.qml"), "instant2_app/qml"),
    (os.path.join(APP, "web", "overlay.html"), "instant2_app/web"),
    (os.path.join(APP, "web", "panel.html"), "instant2_app/web"),
    (os.path.join(ROOT, "scripts", "instant2-update.bat"), "."),
]
binaries = []
hiddenimports = [
    "instant2_app.gui",
    "instant2_app.branding",
    "pyexpat",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PIL.Image",
    "PIL.ImageDraw",
]

for package in (
        "sherpa_onnx", "pystray", "kokoro_onnx", "espeakng_loader"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

datas += copy_metadata("kokoro-onnx")
hiddenimports += ["misaki.espeak"]

a = Analysis(
    [os.path.join(APP, "__main__.py")],
    pathex=[os.path.join(ROOT, "dictado", "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(ROOT, "dictado", "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Instant2-MVP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ROOT, "dictado", "build", "instant2.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Instant2-MVP",
)
