"""Floating overlay: web (QtWebEngine) primero, Tk como respaldo."""
import logging
import math
import queue
import threading

from instant_app.branding import PALETTE, blend

log = logging.getLogger("instant")

# La pastilla del dictado vive fija abajo al centro, estilo asistente de voz:
# siempre en el mismo lugar, sin tapar el campo donde se escribe.
_OVERLAY_BOTTOM_GAP = 60

# Geometria del overlay Tk: la linea base de las barras esta en y=29 y el
# lienzo del medidor mide 26 px de alto, asi que el semialto se queda en 13.
_TK_BAR_BASE = 29
_TK_BAR_MAX = 13
_TK_SPINNER_SEGMENTS = 8


def animation_frame(state, tick, level=0.0):
    """(alturas de barra, fase del spinner) para el renderer Tk.

    El overlay de Qt Quick anima con QML (una onda escalonada por indice al
    escuchar y un arco que barre en 1150 ms al transcribir). Tk no tiene ese
    motor, asi que la misma animacion se calcula por cuadro: las barras suben y
    bajan alternadas con un desfase por indice y el giro avanza en ocho
    segmentos con la cabeza en el color de la marca.

    `level` es el nivel de voz 0..1 del dictado (0 = silencio). Cuando sube,
    las barras centrales crecen hasta ~14 px extra: la pastilla respira con
    la voz en vez de moverse en falso. Con 0 el cuadro es idéntico al de
    antes, así que la regresión existente sigue verde.
    """
    level = max(0.0, min(1.0, float(level)))
    bars = [round(4 + (_TK_BAR_MAX - 4) * (
        0.5 + 0.5 * math.sin(tick * 0.45 - index * 0.75)))
        for index in range(5)]
    if level > 0.0:
        for index in range(5):
            weight = 1.0 - abs(index - 2) / 3.0
            bars[index] = min(_TK_BAR_MAX + 14, round(
                bars[index] + level * 14 * (0.45 + 0.55 * weight)))
    spinner = tick % _TK_SPINNER_SEGMENTS
    return bars, spinner


class OverlayTransitions:
    """Reject stale worker updates before they can replace newer visual states."""
    _RANK = {
        "starting": 0,
        "listening": 1,
        "processing": 2,
        "notice": 3,
        "success": 3,
        "error": 4,
        "idle": 5,
    }

    def __init__(self):
        self.session = -1
        self.rank = -1
        self.state = "idle"
        self.token = 0

    def accept(self, session, state):
        rank = self._RANK[state]
        if session < self.session:
            return None
        if session > self.session:
            self.session = session
            self.rank = -1
            self.state = "idle"
        if rank < self.rank:
            return None
        if self.state == "error" and state != "error":
            return None
        self.rank = rank
        self.state = state
        self.token += 1
        return self.token

    def hide(self):
        self.token += 1
        self.rank = self._RANK["idle"]
        self.state = "idle"
        return self.token


class Overlay:
    """Thread-safe semantic overlay API; platform renderer owns all UI objects."""
    def __init__(self, key_label="F9", style="classic"):
        self._transitions = OverlayTransitions()
        self._lock = threading.Lock()
        self._renderer = self._make_renderer(key_label, style)

    @staticmethod
    def _make_renderer(key_label, style):
        """Web (QtWebEngine) primero; Tk solo si Qt no puede abrir.

        El respaldo cubre máquinas sin QtWebEngine usable o sin PySide6
        (instalaciones viejas) o sin dónde dibujar (X11 sin GL): el daemon
        nunca se queda sin overlay por un fallo del renderer.
        """
        try:
            from instant_app.overlay_web import _WebOverlay
            return _WebOverlay(key_label, style=style)
        except Exception:
            log.warning("el overlay web no abrió; se usa el de Tk",
                        exc_info=True)
            return _TkOverlay(key_label)

    def _send(self, session, state, text="", color=None, milliseconds=0):
        color = color or PALETTE["amber"]
        with self._lock:
            token = self._transitions.accept(session, state)
            if token is None:
                return False
        self._renderer.dispatch((token, session, state, text, color, milliseconds))
        if state in ("processing", "success", "notice", "error", "idle"):
            try:
                self._renderer.set_level(session, 0.0)
            except Exception:
                pass
            try:
                clearer = getattr(self._renderer, "clear_bands", None)
                if clearer is not None:
                    clearer()
            except Exception:
                pass
        return True

    def set_level(self, session, value):
        """Nivel de voz 0..1 para la animación del dictado. No bloquea nunca."""
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if session != self._transitions.session:
                return False
            if self._transitions.state not in ("starting", "listening"):
                return False
        try:
            self._renderer.set_level(session, value)
        except Exception:
            return False
        return True

    def set_bands(self, session, values):
        """Espectro en 9 bandas 0..1 (una por barra). No bloquea nunca."""
        try:
            bands = [max(0.0, min(1.0, float(v))) for v in values]
            if len(bands) != 9:
                return False
        except (TypeError, ValueError):
            return False
        with self._lock:
            if session != self._transitions.session:
                return False
            if self._transitions.state not in ("starting", "listening"):
                return False
        try:
            setter = getattr(self._renderer, "set_bands", None)
            if setter is None:
                return False
            setter(session, bands)
        except Exception:
            return False
        return True

    def set_pitch(self, session, value):
        """Tono 0..1 (grave->agudo) para teñir la onda. No bloquea nunca."""
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if session != self._transitions.session:
                return False
            if self._transitions.state not in ("starting", "listening"):
                return False
        try:
            setter = getattr(self._renderer, "set_pitch", None)
            if setter is None:
                return False
            setter(session, value)
        except Exception:
            return False
        return True

    def starting(self, session):
        return self._send(session, "starting")

    def listening(self, session):
        return self._send(session, "listening")

    def processing(self, session):
        return self._send(session, "processing")

    def success_for(self, session, milliseconds=760):
        return self._send(session, "success", milliseconds=milliseconds)

    def show_notice_for(self, session, text, color=None, milliseconds=2000):
        return self._send(session, "notice", text, color or PALETTE["amber"], milliseconds)

    def show_error_for(self, session, text, color=None, milliseconds=3000):
        return self._send(session, "error", text, color or PALETTE["red"], milliseconds)

    def hide(self):
        with self._lock:
            token = self._transitions.hide()
            session = self._transitions.session
        self._renderer.dispatch((token, session, "idle", "", PALETTE["amber"], 0))

    def run_event_loop(self, shutdown, on_heartbeat):
        return self._renderer.run_event_loop(shutdown, on_heartbeat)


class _TkOverlay:
    """Portable fallback kept for Linux/macOS CLI daemons."""
    _BG = PALETTE["background"]
    _PANEL = PALETTE["surface"]
    _RECORD = PALETTE["accent"]
    _TEXT = PALETTE["text"]
    _LINE = PALETTE["line"]

    def __init__(self, key_label):
        self.key_label = key_label
        self.q = queue.Queue()
        self._current_token = -1
        self._dispatch_lock = threading.Lock()
        self._latest_token = -1
        self.animation_generation = 0
        self._level = 0.0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            try:
                self.root.attributes("-alpha", 0.97)
            except Exception:
                pass
            self.canvas = tk.Canvas(
                self.root, width=184, height=58, bg=self._BG, bd=0,
                highlightthickness=0)
            self.canvas.pack()
            self._draw_overlay()
            self.root.after(50, self._poll)
            self.root.mainloop()
        except Exception:
            log.exception("overlay thread muerto (sin Tk? instala python3-tk en linux)")

    def _draw_overlay(self):
        c = self.canvas
        c.create_polygon(
            12, 1, 172, 1, 183, 12, 183, 46, 172, 57, 12, 57, 1, 46, 1, 12,
            smooth=True, fill=self._PANEL, outline=self._LINE, width=1, tags="panel")
        c.create_oval(13, 13, 45, 45, fill=PALETTE["surface_high"], outline="", tags="icon_bg")
        c.create_arc(7, 7, 51, 51, start=40, extent=80, style="arc",
                     outline=self._RECORD, width=2, tags="record_ring")
        c.create_arc(7, 7, 51, 51, start=220, extent=80, style="arc",
                     outline=self._RECORD, width=2, tags="record_ring")
        c.create_rectangle(26, 19, 32, 32, fill=self._TEXT, outline="", tags="mic")
        c.create_arc(22, 23, 36, 37, start=180, extent=180, style="arc",
                     outline=self._TEXT, width=2, tags="mic")
        c.create_line(29, 36, 29, 40, fill=self._TEXT, width=2, tags="mic")
        c.create_line(25, 40, 33, 40, fill=self._TEXT, width=2, tags="mic")
        self.bar_ids = [c.create_line(
            x, 29, x, 29, fill=self._RECORD, width=4, capstyle="round",
            tags="record_bar") for x in (75, 86, 97, 108, 119)]
        self.spinner_ids = []
        for i in range(8):
            angle = i * math.tau / 8
            x, y = 97 + math.cos(angle) * 13, 29 + math.sin(angle) * 13
            self.spinner_ids.append(c.create_oval(
                x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                fill=PALETTE["surface_high"], outline="", tags="spinner"))
        self.message_id = c.create_text(
            60, 29, anchor="w", width=350, text="", font=("Segoe UI", 10, "bold"),
            fill=self._TEXT, tags="message")

    def dispatch(self, event):
        token = event[0]
        with self._dispatch_lock:
            self._latest_token = max(self._latest_token, token)
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self.q.put(event)

    # Sin guarda de sesion a proposito: el wrapper Overlay.set_level ya
    # rechaza sesiones viejas; aqui solo se coalescea el maximo pendiente.
    def set_level(self, session, value):
        del session
        try:
            level = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return
        with self._dispatch_lock:
            # C1: el wrapper pone a cero al entrar en processing/success/
            # notice/error/idle; antes max() volvia el 0.0 no-op y la pastilla
            # conservaba una cola que decaia (~0.82 cada 90 ms). Asignar es
            # trivial y correcto: el max-coalesce solo importa subiendo.
            self._level = level if level == 0.0 else max(self._level, level)

    def set_bands(self, session, values):
        # El fallback Tk mueve sus 5 barras con el nivel global (set_level);
        # el espectro por banda es un lujo del renderer Qt Quick.
        del session, values

    def set_pitch(self, session, value):
        # Tono: también exclusivo del renderer Qt Quick.
        del session, value

    def clear_bands(self):
        pass

    def _poll(self):
        try:
            while True:
                token, _session, state, text, color, milliseconds = self.q.get_nowait()
                if token < self._current_token:
                    continue
                self._current_token = token
                if state == "idle":
                    self.animation_generation += 1
                    self.root.withdraw()
                    continue
                if state in ("starting", "listening"):
                    self._show_state("recording")
                elif state == "processing":
                    self._show_state("transcribing")
                else:
                    self._show_feedback("Copiado" if state == "success" else text, color)
                # Posición fija abajo al centro, como en Qt: mismo lugar siempre.
                width, height = (430, 68) if self.state == "feedback" else (184, 58)
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                x, y = (sw - width) // 2, sh - height - 96
                # Nace 18 px abajo y sube, en espejo al overlay Qt.
                self.root.geometry(f"+{max(0, x)}+{max(0, y + 18)}")
                try:
                    self.root.attributes("-alpha", 0.0)
                except Exception:
                    pass
                self.root.deiconify()
                self.root.lift()
                generation = self.animation_generation
                self._fade_to(generation, 0.97)
                self._slide_to(generation, max(0, y))
                if milliseconds:
                    self.root.after(milliseconds, lambda t=token: self._expire(t))
        except queue.Empty:
            pass
        except Exception:
            log.exception("overlay poll fail")
        self.root.after(50, self._poll)

    def _show_state(self, state):
        self.state = state
        self.animation_generation += 1
        generation = self.animation_generation
        self.tick = 0
        with self._dispatch_lock:
            self._level = 0.0
        self.canvas.coords(
            "panel", 12, 1, 172, 1, 183, 12, 183, 46, 172, 57,
            12, 57, 1, 46, 1, 12)
        self.canvas.config(width=184, height=58, bg=self._BG)
        self.canvas.itemconfigure("panel", fill=self._PANEL, outline=self._LINE)
        self.canvas.itemconfigure("message", text="")
        self.canvas.itemconfigure("record_ring", state="normal" if state == "recording" else "hidden")
        self.canvas.itemconfigure("mic", state="normal" if state == "recording" else "hidden")
        self.canvas.itemconfigure("icon_bg", state="normal" if state == "recording" else "hidden")
        self.canvas.itemconfigure("record_bar", state="normal" if state == "recording" else "hidden")
        self.canvas.itemconfigure("spinner", state="normal" if state == "transcribing" else "hidden")
        self._animate(generation)

    def _show_feedback(self, text, color):
        self.animation_generation += 1
        self.state = "feedback"
        with self._dispatch_lock:
            self._level = 0.0
        self.canvas.config(width=430, height=68, bg=self._BG)
        self.canvas.itemconfigure("panel", fill=self._PANEL, outline=self._LINE)
        for tag in ("record_ring", "mic", "icon_bg", "record_bar", "spinner"):
            self.canvas.itemconfigure(tag, state="hidden")
        self.canvas.coords("panel", 12, 1, 418, 1, 429, 12, 429, 56, 418, 67, 12, 67, 1, 56, 1, 12)
        self.canvas.itemconfigure("message", text=text, fill=color, width=390)
        self.canvas.coords("message", 18, 34)

    def _animate(self, generation):
        if generation != self.animation_generation:
            return
        with self._dispatch_lock:
            level = self._level
            self._level *= 0.82
        bars, spinner = animation_frame(self.state, self.tick, level)
        if self.state == "recording":
            self.canvas.itemconfigure(
                "panel", outline=blend(self._LINE, PALETTE["accent"], 0.55))
            for item, height in zip(self.bar_ids, bars):
                x = self.canvas.coords(item)[0]
                self.canvas.coords(item, x, 29 - height, x, 29 + height)
        else:
            self.canvas.itemconfigure(
                "panel", outline=blend(self._LINE, PALETTE["working"], 0.55))
            # La cabeza del giro usa el acento y el resto se apaga hacia el fondo.
            for i, item in enumerate(self.spinner_ids):
                phase = ((i - spinner) % _TK_SPINNER_SEGMENTS) / (_TK_SPINNER_SEGMENTS - 1)
                self.canvas.itemconfigure(
                    item, fill=blend(PALETTE["accent"], PALETTE["surface_high"], phase))
        self.tick += 1
        self.root.after(90, lambda g=generation: self._animate(g))

    def _fade_to(self, generation, target):
        """Fundido de entrada/salida del fallback Tk."""
        if generation != self.animation_generation:
            return
        try:
            current = float(self.root.attributes("-alpha"))
        except Exception:
            return
        if abs(target - current) < 0.05:
            try:
                self.root.attributes("-alpha", target)
            except Exception:
                pass
            if target == 0.0:
                self.root.withdraw()
            return
        step = 0.2 if target > current else -0.2
        try:
            self.root.attributes("-alpha", max(0.0, min(0.97, current + step)))
        except Exception:
            return
        self.root.after(30, lambda g=generation: self._fade_to(g, target))

    def _expire(self, token):
        with self._dispatch_lock:
            current = token == self._current_token == self._latest_token
        if current:
            self._current_token += 1
            self.animation_generation += 1
            generation = self.animation_generation
            self._fade_to(generation, 0.0)
            try:
                self._slide_to(generation, self.root.winfo_y() + 14)
            except Exception:
                pass

    def _slide_to(self, generation, target_y):
        """Elevación de entrada/salida del fallback Tk (3 px por cuadro)."""
        if generation != self.animation_generation:
            return
        try:
            current_y = self.root.winfo_y()
        except Exception:
            return
        if abs(target_y - current_y) <= 3:
            try:
                x = self.root.winfo_x()
                self.root.geometry(f"+{x}+{target_y}")
            except Exception:
                pass
            return
        step = 3 if target_y > current_y else -3
        try:
            x = self.root.winfo_x()
            self.root.geometry(f"+{x}+{current_y + step}")
        except Exception:
            return
        self.root.after(30, lambda g=generation: self._slide_to(g, target_y))

    def run_event_loop(self, _shutdown, _on_heartbeat):
        return False
