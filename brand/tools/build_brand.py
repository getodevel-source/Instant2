"""Genera los assets de marca de Instant2 a partir de una sola geometría.

Fuente de verdad: `instant2_app.branding` (paleta) y `MARK_BARS` (símbolo).
Salidas: SVG en `brand/logo/`, PNG en `brand/png/`, ICO en `brand/ico/` y
`dictado/build/instant2.ico`, y la imagen social en `brand/social/`.

Requiere Chrome o Edge para rasterizar (se detecta solo) y Pillow. El texto
del logotipo se convierte a curvas con fontTools a partir de Manrope, así que
los SVG no dependen de la fuente instalada.

Uso:
    python brand/tools/build_brand.py            # todo
    python brand/tools/build_brand.py --svg      # solo vectores
    python brand/tools/build_brand.py --png      # solo rasterizado
"""

from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "brand"
LOGO = BRAND / "logo"
PNG = BRAND / "png"
ICO = BRAND / "ico"
SOCIAL = BRAND / "social"
FONT = ROOT / "dictado" / "src" / "instant2_app" / "web" / "Manrope.ttf"

sys.path.insert(0, str(ROOT / "dictado" / "src"))
from instant2_app.branding import (  # noqa: E402
    MARK_BAR_WIDTH, MARK_BARS, MARK_BOX, PALETTE,
)

# --- Marca -----------------------------------------------------------------

MINT = PALETTE["panel_accent"]          # #86dbc9
INK = PALETTE["bg_alt"]                 # #0c1418
FOG = PALETTE["panel_text"]             # #edf3f2
MUTED = PALETTE["panel_muted"]          # #b0bfbe
WORDMARK_WEIGHT = 700
WORDMARK_TRACKING = -0.035              # em
# Ajustes ópticos por par, en unidades de fuente (upm 2000). El texto se
# compone sin HarfBuzz; estos pares reemplazan el kern del GPOS de Manrope.
KERN = {
    ("I", "n"): -18,
    ("n", "s"): 0,
    ("s", "t"): -18,
    ("t", "a"): -26,
    ("a", "n"): 0,
    ("n", "t"): -14,
    ("t", "2"): -34,
}
MARK_BOUNDS = (
    min(x for x, _ in MARK_BARS),
    min((MARK_BOX - height) / 2 for _, height in MARK_BARS),
    max(x + MARK_BAR_WIDTH for x, _ in MARK_BARS),
    max((MARK_BOX + height) / 2 for _, height in MARK_BARS),
)


def wordmark(weight=WORDMARK_WEIGHT, tracking=WORDMARK_TRACKING, text="Instant2"):
    """Devuelve el trazado de 'Instant2' en curvas y su caja óptica."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = instantiateVariableFont(
        TTFont(str(FONT)), {"wght": weight}, inplace=False)
    upm = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    advances = font["hmtx"]
    scale = 100.0 / cap
    tracking_units = tracking * upm

    cursor = 0.0
    commands: list[str] = []
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for index, char in enumerate(text):
        name = cmap[ord(char)]
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(TransformPen(pen, (1, 0, 0, -1, cursor, 0)))
        commands.append(pen.getCommands())

        bounds = BoundsPen(glyphs)
        glyphs[name].draw(TransformPen(bounds, (1, 0, 0, -1, cursor, 0)))
        if bounds.bounds:
            x_min, y_min, x_max, y_max = bounds.bounds
            min_x, min_y = min(min_x, x_min), min(min_y, y_min)
            max_x, max_y = max(max_x, x_max), max(max_y, y_max)

        cursor += advances[name][0] + tracking_units
        if index + 1 < len(text):
            cursor += KERN.get((char, text[index + 1]), 0)

    width = (max_x - min_x) * scale
    return {
        "path": " ".join(part for part in commands if part),
        "transform": f"translate({-min_x * scale:.3f} 100) scale({scale:.6f})",
        "width": width,
        "cap": 100.0,
        # Borde real del texto en unidades de marca (0 = alto de mayúscula).
        "top": min_y * scale + 100,
        "bottom": max_y * scale + 100,
        "scale": scale,
    }


def mark_group(fill: str, x=0.0, y=0.0, size=62.0, bar_width=MARK_BAR_WIDTH):
    """Las tres barras como un `<g>` ya escalado a `size` de lado."""
    scale = size / (MARK_BOUNDS[2] - MARK_BOUNDS[0])
    left, top = MARK_BOUNDS[0], MARK_BOUNDS[1]
    radius = bar_width / 2
    rects = []
    for bar_x, height in MARK_BARS:
        bar_y = (MARK_BOX - height) / 2
        rects.append(
            f'<rect x="{bar_x - left:.3f}" y="{bar_y - top:.3f}" '
            f'width="{bar_width}" height="{height}" rx="{radius}" fill="{fill}"/>')
    return (
        f'<g transform="translate({x:.3f} {y:.3f}) scale({scale:.6f})">'
        + "".join(rects) + "</g>")


def mark_svg(fill: str, size=62) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 62 62" role="img" aria-label="Instant2">'
        + mark_group(fill)
        + "</svg>")


def app_icon_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024" role="img" aria-label="Instant2">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.9" y2="1">
      <stop offset="0" stop-color="{PALETTE['icon_bg_start']}"/>
      <stop offset="1" stop-color="{PALETTE['icon_bg_end']}"/>
    </linearGradient>
    <radialGradient id="glow" gradientUnits="userSpaceOnUse" cx="512" cy="430" r="640">
      <stop offset="0" stop-color="{MINT}" stop-opacity="0.09"/>
      <stop offset="0.55" stop-color="{MINT}" stop-opacity="0.03"/>
      <stop offset="1" stop-color="{MINT}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="32" y="32" width="960" height="960" rx="224" fill="url(#bg)"/>
  <rect x="32" y="32" width="960" height="960" rx="224" fill="url(#glow)"/>
  {mark_group(MINT, 512 - 230, 512 - 230, 460)}
</svg>"""


def lockup_svg(theme="dark", layout="horizontal") -> str:
    """Lockup con el logotipo en curvas. `theme` describe el fondo."""
    text_color = FOG if theme == "dark" else INK
    mark_color = MINT if theme == "dark" else INK
    word = wordmark()
    gap = 46.0
    if layout == "horizontal":
        mark_size = 130.0
        mark_top = (100 - mark_size) / 2
        word_x = mark_size + gap
        width = word_x + word["width"]
        height = max(mark_top + mark_size, word["bottom"]) - min(mark_top, word["top"])
        top = min(mark_top, word["top"])
        mark_y = mark_top - top
        word_y = -top
        body = (
            mark_group(mark_color, 0, mark_y, mark_size)
            + f'<g transform="translate({word_x:.3f} {word_y:.3f})">'
            + f'<g transform="{word["transform"]}">'
            + f'<path d="{word["path"]}" fill="{text_color}"/></g></g>')
        return _svg(width, height, body)

    mark_size = 128.0
    top_gap = 40.0
    width = max(mark_size, word["width"])
    mark_x = (width - mark_size) / 2
    word_x = (width - word["width"]) / 2
    word_y = mark_size + top_gap - word["top"]
    height = mark_size + top_gap + word["bottom"] - word["top"]
    body = (
        mark_group(mark_color, mark_x, 0, mark_size)
        + f'<g transform="translate({word_x:.3f} {word_y:.3f})">'
        + f'<g transform="{word["transform"]}">'
        + f'<path d="{word["path"]}" fill="{text_color}"/></g></g>')
    return _svg(width, height, body)


def wordmark_svg(theme="dark") -> str:
    word = wordmark()
    color = FOG if theme == "dark" else INK
    height = word["bottom"] - word["top"]
    body = (
        f'<g transform="translate(0 {-word["top"]:.3f})">'
        f'<g transform="{word["transform"]}">'
        f'<path d="{word["path"]}" fill="{color}"/></g></g>')
    return _svg(word["width"], height, body)


def _svg(width: float, height: float, body: str) -> str:
    width, height = round(width, 3), round(height, 3)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" '
        f'height="{height:g}" viewBox="0 0 {width:g} {height:g}" '
        f'role="img" aria-label="Instant2">{body}</svg>')


def write_svgs():
    LOGO.mkdir(parents=True, exist_ok=True)
    files = {
        "instant2-mark.svg": mark_svg(MINT),
        "instant2-mark-ink.svg": mark_svg(INK),
        "instant2-mark-white.svg": mark_svg("#ffffff"),
        "instant2-app-icon.svg": app_icon_svg(),
        "instant2-wordmark-on-dark.svg": wordmark_svg("dark"),
        "instant2-wordmark-on-light.svg": wordmark_svg("light"),
        "instant2-lockup-horizontal-on-dark.svg": lockup_svg("dark"),
        "instant2-lockup-horizontal-on-light.svg": lockup_svg("light"),
        "instant2-lockup-stacked-on-dark.svg": lockup_svg("dark", "stacked"),
    }
    for name, content in files.items():
        (LOGO / name).write_text(content + "\n", encoding="utf-8")
    print(f"SVG: {len(files)} archivos en {LOGO.relative_to(ROOT)}")


# --- Rasterizado -----------------------------------------------------------

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("chromium") or shutil.which("msedge")
    if not found:
        raise SystemExit("No encontré Chrome/Edge para rasterizar los SVG.")
    return found


def data_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def render_sheet(items, out_dir, gap=24, max_width=2400, scale=1):
    """`items`: (nombre, svg, ancho, alto). Una sola captura y recortes."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    x = y = 0
    row_height = 0
    placed = []
    for name, svg, width, height in items:
        if x and x + width > max_width:
            x, y = 0, y + row_height + gap
            row_height = 0
        placed.append((name, svg, x, y, width, height))
        x += width + gap
        row_height = max(row_height, height)
    page_w = max_width
    page_h = y + row_height
    divs = "".join(
        f'<div style="position:absolute;left:{px}px;top:{py}px;width:{w}px;'
        f'height:{h}px"><img src="{data_uri(svg)}" width="{w}" height="{h}" '
        f'style="display:block;width:{w}px;height:{h}px"></div>'
        for _, svg, px, py, w, h in placed)
    html = (
        '<!doctype html><meta charset="utf-8">'
        '<style>html,body{margin:0;background:transparent;overflow:hidden}</style>'
        + divs)

    tmp = Path(tempfile.mkdtemp(prefix="instant2-brand-"))
    page = tmp / "page.html"
    page.write_text(html, encoding="utf-8")
    shot = tmp / "shot.png"
    subprocess.run([
        chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--force-device-scale-factor={scale}", f"--window-size={page_w},{page_h}",
        "--virtual-time-budget=3000", "--default-background-color=00000000",
        f"--screenshot={shot}", page.as_uri(),
    ], check=True, capture_output=True)
    sheet = Image.open(shot).convert("RGBA")
    for name, _, px, py, w, h in placed:
        crop = sheet.crop((px * scale, py * scale, (px + w) * scale, (py + h) * scale))
        if scale > 1:
            crop = crop.resize((w, h), Image.Resampling.LANCZOS)
        crop.save(out_dir / f"{name}.png")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"PNG: {len(placed)} archivos en {out_dir.relative_to(ROOT)}")


def render_pngs():
    mark_mint = mark_svg(MINT)
    mark_ink = mark_svg(INK)
    icon = app_icon_svg()
    lockup_dark = lockup_svg("dark")
    lockup_light = lockup_svg("light")
    stacked_dark = lockup_svg("dark", "stacked")
    wordmark_dark = wordmark_svg("dark")

    items = []
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        items.append((f"app-icon-{size}", icon, size, size))
    for size in (32, 64, 128, 256, 512):
        items.append((f"mark-mint-{size}", mark_mint, size, size))
        items.append((f"mark-ink-{size}", mark_ink, size, size))
    items.append(("favicon-32", icon, 32, 32))
    items.append(("lockup-horizontal-on-dark-1200", lockup_dark, 1200, None))
    items.append(("lockup-horizontal-on-light-1200", lockup_light, 1200, None))
    items.append(("lockup-stacked-on-dark-640", stacked_dark, 640, None))
    items.append(("wordmark-on-dark-1200", wordmark_dark, 1200, None))

    def height_for(svg, width):
        head = svg.split(">", 1)[0]
        import re
        height = float(re.search(r'height="([\d.]+)"', head).group(1))
        view_w = float(re.search(r'viewBox="0 0 ([\d.]+)', head).group(1))
        return round(height * width / view_w)

    items = [(name, svg, w, h if h else height_for(svg, w)) for name, svg, w, h in items]
    render_sheet(items, PNG, scale=2)


def render_icon():
    """Empaqueta el ICO con el mismo dibujo que usa la bandeja en runtime."""
    from instant2_app.branding import create_icon_image

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    ICO.mkdir(parents=True, exist_ok=True)
    target = ICO / "instant2.ico"
    create_icon_image(256).save(target, format="ICO", sizes=sizes)
    shutil.copyfile(target, ROOT / "dictado" / "build" / "instant2.ico")
    print(f"ICO: {target.relative_to(ROOT)} + dictado/build/instant2.ico")


def render_og():
    from PIL import Image

    SOCIAL.mkdir(parents=True, exist_ok=True)
    lockup = lockup_svg("dark")
    icon = app_icon_svg()
    bars = "".join(
        f'<span style="width:3px;height:{h}px;border-radius:2px;'
        f'background:{MINT};opacity:{o}"></span>'
        for h, o in ((14, .55), (26, .8), (38, 1), (22, .75), (46, 1),
                     (30, .85), (17, .6), (24, .7), (12, .5)))
    html = f"""<!doctype html><meta charset="utf-8">
<style>
  @font-face {{ font-family: Manrope; src: url("data:font/ttf;base64,{base64.b64encode(FONT.read_bytes()).decode()}") format("truetype");
               font-weight: 200 800; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; width: 1200px; height: 630px; overflow: hidden; }}
  body {{ position: relative; background: {INK}; color: {FOG};
          font-family: Manrope, "Segoe UI", sans-serif; }}
  .wash {{ position: absolute; inset: -20%; background:
          radial-gradient(60% 60% at 78% 30%, rgba(134,219,201,.16), transparent 70%),
          radial-gradient(50% 50% at 12% 92%, rgba(36,121,107,.22), transparent 70%); }}
  .rule {{ position: absolute; inset: 0; opacity: .22; background-image:
          linear-gradient(rgba(134,219,201,.14) 1px, transparent 1px),
          linear-gradient(90deg, rgba(134,219,201,.14) 1px, transparent 1px);
          background-size: 64px 64px; mask-image: radial-gradient(60% 80% at 70% 30%, #000, transparent 75%); }}
  .content {{ position: absolute; inset: 0; padding: 78px 84px; display: flex;
              flex-direction: column; justify-content: space-between; }}
  .logo img {{ display: block; width: 430px; height: auto; }}
  h1 {{ margin: 26px 0 14px; font-size: 68px; line-height: 1.02;
        letter-spacing: -.055em; font-weight: 550; }}
  p {{ margin: 0; max-width: 520px; color: {MUTED}; font-size: 23px;
       line-height: 1.45; font-weight: 450; }}
  .pill {{ position: absolute; right: 84px; top: 196px; width: 344px; height: 56px;
           border-radius: 28px; background: rgba(22,36,43,.92);
           border: 1px solid {PALETTE['line']}; display: flex; align-items: center;
           gap: 14px; padding: 0 20px; box-shadow: 0 18px 50px rgba(0,0,0,.35); }}
  .bars {{ display: flex; align-items: center; gap: 3px; height: 46px; }}
  .label {{ color: {FOG}; font-size: 13px; font-weight: 600; letter-spacing: -.01em; }}
  .key {{ margin-left: auto; border: 1px solid {PALETTE['line']};
          border-radius: 7px; padding: 4px 8px; color: {MINT}; font-size: 10px;
          font-weight: 700; background: rgba(12,20,24,.7); }}
  .foot {{ display: flex; justify-content: space-between; align-items: center;
           color: {PALETTE['panel_faint']}; font-size: 16px; letter-spacing: .01em; }}
  .foot b {{ color: {MINT}; font-weight: 600; }}
  .orb {{ position: absolute; right: -200px; bottom: -330px; width: 560px;
          height: 560px; opacity: .07; }}
</style>
<div class="wash"></div><div class="rule"></div>
<img class="orb" src="{data_uri(icon)}">
<div class="content">
  <div>
    <div class="logo"><img src="{data_uri(lockup)}"></div>
    <h1>Tu voz,<br>en texto.</h1>
    <p>Dictado por voz en tu equipo. Mantené F9, hablá y soltá: el texto aparece donde estás escribiendo.</p>
  </div>
  <div class="foot"><span>Dictado privado · Windows, Linux y macOS</span><span><b>F9</b> para hablar</span></div>
</div>
<div class="pill"><div class="bars">{bars}</div><span class="label">Escuchando</span><span class="key">F9</span></div>
"""
    tmp = Path(tempfile.mkdtemp(prefix="instant2-brand-"))
    page = tmp / "og.html"
    page.write_text(html, encoding="utf-8")
    shot = tmp / "og.png"
    subprocess.run([
        chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--allow-file-access-from-files", "--force-device-scale-factor=2",
        "--window-size=1200,630", "--virtual-time-budget=3000",
        "--default-background-color=00000000", f"--screenshot={shot}",
        page.as_uri(),
    ], check=True, capture_output=True)
    with Image.open(shot) as image:
        image.convert("RGB").resize((1200, 630), Image.Resampling.LANCZOS).save(
            SOCIAL / "og-image.png")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OG: {(SOCIAL / 'og-image.png').relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--svg", action="store_true", help="solo vectores")
    parser.add_argument("--png", action="store_true", help="solo PNG/ICO/OG")
    args = parser.parse_args()
    if not (args.png or args.svg):
        args.svg = args.png = True
    if args.svg:
        write_svgs()
    if args.png:
        render_pngs()
        render_icon()
        render_og()


if __name__ == "__main__":
    main()
