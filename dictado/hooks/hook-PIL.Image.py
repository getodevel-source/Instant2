"""Pillow hook: Instant draws its icon and only serializes it as PNG."""

# PyInstaller's default hook adds every Pillow format plugin. Image.init()
# loads those plugins dynamically, but Instant neither opens user images nor
# exposes image conversion; the generated app and tray icons use PNG only.
hiddenimports = ["PIL.PngImagePlugin"]
