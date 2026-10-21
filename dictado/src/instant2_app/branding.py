"""Identidad gráfica de Instant2, compartida por la ventana y la bandeja.

`PALETTE` es la fuente de los colores de la interfaz web. Sus compositores
inyectan los tokens del panel y del indicador antes de cargar las páginas.
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
    "panel_background": "#10191d",
    "panel_bg_alt": "#0c1418",
    "panel_surface": "#162126",
    "panel_surface_alt": "#1b292e",
    "panel_surface_high": "#223238",
    "panel_line": "#34474e",
    "panel_line_soft": "#26363c",
    "panel_text": "#edf3f2",
    "panel_muted": "#b0bfbe",
    "panel_faint": "#91a4a5",
    "panel_accent": "#86dbc9",
    "panel_accent_soft": "#b8ebde",
    "panel_accent_button": "#24796b",
    "panel_accent_hover": "#2b8979",
    "panel_accent_press": "#1b655a",
    "panel_brand_mid": "#164b4d",
    "panel_brand_start": "#138d7d",
    "panel_brand_end": "#086b64",
    "panel_brand_hover_start": "#19a08c",
    "panel_brand_hover_end": "#0a7a70",
    "panel_hero_start": "#162126",
    "panel_hero_end": "#10191d",
    # Icono de la aplicación: petróleo profundo con una pizca de verde.
    "icon_bg_start": "#17333c",
    "icon_bg_end": "#0f2228",
    "icon_bg": "#132a32",
}

# Geometría de la marca: tres barras ("señal") dentro de una caja de 100 x 100.
# La barra central es la voz; las laterales, lo que entra y lo que sale.
# `MARK_BARS` guarda (x, alto); todas se centran verticalmente en la caja.
MARK_BOX = 100.0
MARK_BAR_WIDTH = 16.0
MARK_BARS = ((19.0, 42.0), (42.0, 62.0), (65.0, 50.0))


def rgba(color, alpha, channel=None):
    """`#rrggbb` + alfa -> `rgba(r, g, b, a)`, el formato que entiende QSS."""
    value = color.lstrip("#")
    if channel is not None:
        value = value[channel * 2:channel * 2 + 2] * 3
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


def panel_css_tokens():
    """Render the panel's palette at composition time, without a second copy."""
    names = {
        "bg": "background", "bg-alt": "bg_alt", "surface": "surface",
        "surface-alt": "surface_alt", "surface-high": "surface_high",
        "line": "line", "line-soft": "line_soft", "text": "text",
        "muted": "muted", "faint": "faint", "accent": "accent",
        "accent-soft": "accent_soft", "accent-button": "accent_button",
        "accent-hover": "accent_hover", "accent-press": "accent_press",
    }
    tokens = {name: PALETTE["panel_" + key] for name, key in names.items()}
    tokens.update({name: PALETTE[name] for name in ("green", "amber", "red")})
    return "\n".join(f"  --{name}: {value};" for name, value in tokens.items())


def overlay_tokens():
    """Colors consumed by the Canvas renderer, resolved from the same palette."""
    return {name: PALETTE[name] for name in (
        "accent", "working", "green", "amber", "red", "text", "muted",
        "glass", "glass_alt", "line")}


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
    """Dibuja el icono de la app (petróleo + señal) en cualquier tamaño.

    Reproduce la geometría de `brand/logo/instant2-app-icon.svg`: squircle al
    3,1 % del borde, radio del 21,9 % y las tres barras de `MARK_BARS`. El
    degradado del SVG se sustituye por su tono medio, que es lo único que se
    distingue por debajo de 32 px.
    """
    from importlib import import_module

    Image = import_module("PIL.Image")
    ImageDraw = import_module("PIL.ImageDraw")

    render_size = size * 4
    unit = render_size / 1024
    image = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = round(32 * unit)
    draw.rounded_rectangle(
        (inset, inset, render_size - inset, render_size - inset),
        radius=round(224 * unit), fill=PALETTE["icon_bg"])

    # El símbolo ocupa 460 de los 1024 px del icono (ver app-icon.svg).
    mark_size = 460 * unit
    mark_left = min(x for x, _ in MARK_BARS)
    mark_top = min((MARK_BOX - height) / 2 for _, height in MARK_BARS)
    mark_right = max(x + MARK_BAR_WIDTH for x, _ in MARK_BARS)
    mark_scale = mark_size / (mark_right - mark_left)
    origin_x = (render_size - mark_size) / 2
    origin_y = (render_size - mark_size) / 2
    for x, height in MARK_BARS:
        left = round(origin_x + (x - mark_left) * mark_scale)
        top = round(origin_y + ((MARK_BOX - height) / 2 - mark_top) * mark_scale)
        right = round(origin_x + (x - mark_left + MARK_BAR_WIDTH) * mark_scale)
        bottom = round(origin_y + ((MARK_BOX + height) / 2 - mark_top) * mark_scale)
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=round(MARK_BAR_WIDTH * mark_scale / 2),
            fill=PALETTE["panel_accent"])

    resampling = getattr(Image, "Resampling", Image)
    return image.resize((size, size), resampling.LANCZOS)
