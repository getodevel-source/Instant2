"""Exercise real Windows Ctrl+V/Ctrl+C in disposable native and web fields.

The original clipboard MIME data stays in RAM and is restored only while the
clipboard still belongs to this audit. No personal clipboard content is logged.
Run on an unlocked Windows desktop: python dictado/bench/desktop_delivery_smoke.py
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    if sys.platform != "win32":
        raise SystemExit("This desktop smoke requires Windows.")
    from PySide6.QtCore import QMimeData, QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from instant2_app import paste, selection
    from instant2_app.clipboard import sequence_number

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=".audit-eight/desktop-delivery.json")
    parser.add_argument("--manual-focus", action="store_true", help="Wait for the audit window to receive native focus")
    args = parser.parse_args()
    app = QApplication(["Instant2 delivery audit"])
    clipboard = app.clipboard()
    original = QMimeData()
    mime = clipboard.mimeData()
    if mime is not None:
        for name in mime.formats():
            original.setData(name, mime.data(name))
    window = QWidget()
    window.setWindowTitle("Instant2 - disposable delivery audit")
    window.resize(720, 440)
    layout = QVBoxLayout(window)
    single, multi, web = QLineEdit(), QPlainTextEdit(), QWebEngineView()
    for field in (single, multi, web):
        layout.addWidget(field)
    loaded = []
    web.loadFinished.connect(loaded.append)
    web.setHtml('<html lang="es"><body><textarea id="target" style="width:95%;height:100px"></textarea></body></html>')
    window.show()
    window.activateWindow()
    reports = []
    audit_sequence = None

    def wait_until(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        raise AssertionError("Desktop audit timed out")

    def pause(seconds):
        until = time.monotonic() + seconds
        wait_until(lambda: time.monotonic() >= until, seconds + 1)

    def js(script):
        result = []
        web.page().runJavaScript(script, lambda value: result.append(value))
        wait_until(lambda: bool(result))
        return result[0]

    def background(function):
        results, errors = [], []
        def run():
            try:
                results.append(function())
            except Exception as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        wait_until(lambda: not thread.is_alive())
        assert not errors, errors
        return results[0]

    text = "Prueba local: mañana, acción y número 42. "
    try:
        import ctypes
        from ctypes import wintypes
        foreground = ctypes.windll.user32.GetForegroundWindow
        foreground.restype = wintypes.HWND
        native_focus = lambda: foreground() == int(window.winId())
        print("Waiting for native focus on: Instant2 - disposable delivery audit", flush=True)
        wait_until(lambda: loaded and loaded[-1] and native_focus(), 45 if args.manual_focus else 5)
        for name, field in (("line", single), ("multiline", multi), ("web", web)):
            window.activateWindow()
            field.setFocus()
            if name == "web":
                js("document.getElementById('target').focus(); true")
            wait_until(lambda: native_focus() and field.hasFocus())
            pause(.08)
            clipboard.setText("instant2-audit-before")
            audit_sequence = sequence_number()
            start = time.perf_counter()
            assert native_focus(), "Native focus changed before delivery"
            background(lambda: paste.paste(text))
            duration = time.perf_counter() - start
            actual = js("document.getElementById('target').value") if name == "web" else field.text() if name == "line" else field.toPlainText()
            assert actual == text, (name, "delivery mismatch")
            assert clipboard.text() == "instant2-audit-before", "Original text was not restored"
            audit_sequence = sequence_number()
            if name == "web":
                js("document.getElementById('target').select(); true")
            else:
                field.selectAll()
            clipboard.setText("instant2-audit-before-selection")
            selected = background(selection.copy_selected_text)
            assert selected == text, (name, "selection mismatch")
            assert clipboard.text() == "instant2-audit-before-selection"
            audit_sequence = sequence_number()
            reports.append({"field": name, "paste_seconds": round(duration, 4), "paste": "PASS", "selection": "PASS"})

        single.clear()
        single.setFocus()
        clipboard.setText("instant2-audit-before")
        QTimer.singleShot(150, lambda: clipboard.setText("instant2-audit-new-copy"))
        background(lambda: paste.paste(text))
        assert single.text() == text
        assert clipboard.text() == "instant2-audit-new-copy", "Concurrent user copy was overwritten"
        audit_sequence = sequence_number()
        reports.append({"scenario": "concurrent-copy-during-paste", "result": "PASS"})
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"platform": "win32", "result": "PASS", "cases": reports}, indent=2), encoding="utf-8")
        print(json.dumps({"result": "PASS", "cases": len(reports), "report": str(target)}))
    finally:
        if audit_sequence is not None and sequence_number() == audit_sequence:
            clipboard.setMimeData(original)
        window.close()
        app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
