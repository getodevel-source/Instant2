"""Pillow hook: bundle only formats used by Instant2's generated icons."""

# PyInstaller's default hook adds every Pillow format plugin. Image.init()
# loads them dynamically, but frozen builds need explicit imports for the
# generated window icon (PNG) and pystray's Windows serialization (ICO).
hiddenimports = ["PIL.PngImagePlugin", "PIL.IcoImagePlugin"]
