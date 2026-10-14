"""Identidad gráfica de Instant2, compartida por la ventana y la bandeja.

`PALETTE` es la única fuente de verdad de los colores de la aplicación. El QML
de la pastilla flotante repite estos valores porque se carga sin motor de
plantillas (ver `tests/test_overlay_palette.py`, que falla si se desincronizan).
"""

# Familia de marca: verde agua (marca) sobre azul petróleo profundo.
PALETTE = {
    # Superficies
    "background": "#0f1a20",
    "bg_alt": "#0c161b",
    "surface": "#16242b",
    "surface_alt": "#1d2f37",
    "surface_high": "#243a43",
    "glass": "#1b2b34",
    "glass_alt": "#0f1c23",
    "line": "#28414a",
    "line_soft": "#1e333b",
    # Texto
    "text": "#eaf3f4",
    "muted": "#9db0b6",
    "faint": "#6f858c",
    # Marca
    "accent": "#55d5c8",
    "accent_soft": "#72d5c7",
    "accent_button": "#0d7d73",
    "accent_hover": "#109287",
    "accent_press": "#0a655e",
    "hero": "#15333d",
    "hero_alt": "#112830",
    "hero_text": "#f2f8f8",
    # Estados
    "green": "#7cdda6",
    "amber": "#f2c36a",
    "red": "#ff908b",
    "working": "#8fd7e8",
    # Superficies dedicadas al panel principal; el overlay mantiene su piel
    # oscura translúcida y usa los tokens de marca/estado compartidos.
    "panel_background": "#0b1418",
    "panel_bg_alt": "#0e1b20",
    "panel_surface": "#132228",
    "panel_surface_alt": "#192b32",
    "panel_surface_high": "#223a42",
    "panel_line": "#36535c",
    "panel_line_soft": "#20353c",
    "panel_text": "#f0f7f5",
    "panel_muted": "#aec0c0",
    "panel_faint": "#84999b",
    "panel_accent": "#6be2d1",
    "panel_accent_soft": "#a0eee1",
    "panel_accent_button": "#087f73",
    "panel_accent_hover": "#099889",
    "panel_accent_press": "#066d64",
    "panel_brand_mid": "#164b4d",
    "panel_brand_start": "#138d7d",
    "panel_brand_end": "#086b64",
    "panel_brand_hover_start": "#19a08c",
    "panel_brand_hover_end": "#0a7a70",
    "panel_hero_start": "#172d34",
    "panel_hero_end": "#102127",
}


def rgba(color, alpha, channel=None):
    """`#rrggbb` + alfa -> `rgba(r, g, b, a)`, el formato que entiende QSS."""
    value = color.lstrip("#")
    if channel is not None:
        value = value[channel * 2:channel * 2 + 2] * 3
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def blend(start, end, ratio):
    """Mezcla dos `#rrggbb`; `ratio` 0 deja `start` y 1 deja `end`."""
    ratio = min(1.0, max(0.0, ratio))
    channels = []
    for index in (0, 2, 4):
        begin = int(start.lstrip("#")[index:index + 2], 16)
        finish = int(end.lstrip("#")[index:index + 2], 16)
        channels.append(round(begin + (finish - begin) * ratio))
    return "#" + "".join(f"{value:02x}" for value in channels)


def create_icon_image(size=64):
    """Renderiza una marca de micrófono limpia en cualquier tamaño."""
    from importlib import import_module

    Image = import_module("PIL.Image")
    ImageDraw = import_module("PIL.ImageDraw")

    render_size = size * 4
    scale = render_size / 256
    image = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(value * scale) for value in values)

    background = "#173b49"
    mic = PALETTE["accent_soft"]
    light = "#f1f6f6"
    draw.rounded_rectangle(
        box((10, 10, 246, 246)), radius=round(54 * scale), fill=background)
    draw.rounded_rectangle(
        box((96, 42, 160, 150)), radius=round(32 * scale), fill=mic)
    stroke = max(1, round(14 * scale))
    draw.line(box((68, 104, 68, 142)), fill=light, width=stroke)
    draw.line(box((188, 104, 188, 142)), fill=light, width=stroke)
    draw.arc(box((68, 82, 188, 202)), start=0, end=180, fill=light, width=stroke)
    draw.rounded_rectangle(
        box((121, 196, 135, 225)), radius=round(7 * scale), fill=light)
    draw.rounded_rectangle(
        box((91, 220, 165, 234)), radius=round(7 * scale), fill=light)
    resampling = getattr(Image, "Resampling", Image)
    return image.resize((size, size), resampling.LANCZOS)
