"""Smoke del overlay web de produccion: instancia el renderer real del daemon
(_WebOverlay) con una secuencia sintetica de dictado y captura la ventana.

Uso: python web_overlay_smoke.py [--style classic|orbital]
"""
import argparse
import json
import logging
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "src")))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401  (wrapper de grabWindow)

from instant2_app.overlay_web import _WebOverlay  # noqa: E402

assert callable(QQuickWindow.grabWindow)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--style", default="orbital", choices=["classic", "orbital"])
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    overlay = _WebOverlay("F9", style=args.style)
    out_path = args.out or os.path.join(
        HERE, "bakeoff", "out", f"web_overlay_{args.style}.png")

    token = [0]

    def dispatch(state, text="", ms=0):
        token[0] += 1
        overlay.dispatch((token[0], 1, state, text, "#f2c36a", ms))

    feed_on = [False]

    def feed():
        if not feed_on[0]:
            return
        t = time.monotonic()
        overlay.set_level(1, 0.55 + 0.35 * math.sin(t * 5.0))
        overlay.set_bands(1, [max(0.0, 0.45 + 0.4 * math.sin(t * 3.7 - i * 0.8))
                              for i in range(9)])
        overlay.set_pitch(1, 0.45 + 0.15 * math.sin(t * 1.3))

    feed_timer = QTimer()
    feed_timer.setInterval(40)
    feed_timer.timeout.connect(feed)
    feed_timer.start()

    captures = []

    def capture(tag):
        image = overlay.root.grabWindow()
        path = out_path.replace(".png", f"_{tag}.png")
        image.save(path)
        mid = image.pixelColor(image.width() // 2, image.height() - 12 - 100)
        captures.append({"tag": tag, "file": path, "size": [image.width(), image.height()],
                         "content_rgba": [mid.red(), mid.green(), mid.blue(), mid.alpha()]})

    def finish():
        capture("final")
        page = overlay.view.property("loadProgress") if overlay.view else None
        title = overlay.view.property("title") if overlay.view else None
        print(json.dumps({"event": "captured", "style": args.style,
                          "captures": captures, "ready": overlay._ready,
                          "loadProgress": page, "title": title}), flush=True)
        overlay.application.quit()



    QTimer.singleShot(300, lambda: dispatch("starting"))
    QTimer.singleShot(900, lambda: (feed_on.__setitem__(0, True), dispatch("listening")))
    QTimer.singleShot(2600, lambda: capture("listening"))
    QTimer.singleShot(3200, lambda: (feed_on.__setitem__(0, False), dispatch("processing")))
    QTimer.singleShot(4400, lambda: dispatch("success", ms=900))
    QTimer.singleShot(5800, lambda: dispatch("notice", "Listo", ms=2500))
    QTimer.singleShot(6300, lambda: capture("notice"))
    QTimer.singleShot(7000, finish)

    overlay.application.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
