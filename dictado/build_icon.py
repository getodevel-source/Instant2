"""Genera el recurso .ico usado por PyInstaller en Windows."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from instant2_app.branding import create_icon_image


output = ROOT / "build" / "instant2.ico"
output.parent.mkdir(parents=True, exist_ok=True)
create_icon_image(256).save(
    output,
    format="ICO",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print(output)
