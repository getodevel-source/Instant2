"""Panel reading actions; the host supplies its existing state and workers."""
import logging
import threading

log = logging.getLogger("instant2")


class PanelReadingActions:
    def _schedule_tts_idle_unload(self):
        from instant2_app.resource_policy import idle_unload_seconds

        seconds = idle_unload_seconds(self.cfg)
        timer, self._tts_idle_timer = self._tts_idle_timer, None
        if timer is not None:
            timer.cancel()
        if self._closed or seconds <= 0 or self._tts_reader is None:
            return
        timer = threading.Timer(seconds, self._unload_tts_if_idle)
        timer.daemon = True
        self._tts_idle_timer = timer
        timer.start()

    def _unload_tts_if_idle(self):
        if self._closed:
            return
        self._tts_idle_timer = None
        if self._tts_cancel_event is not None or self._tts_state.get("busy"):
            self._schedule_tts_idle_unload()
            return
        reader = self._tts_reader
        if reader is not None and reader.unload():
            log.info("modelo de lectura liberado tras el periodo de inactividad.")

    def _op_tts_play(self, message):
        from instant2_app import tts

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            self._tts_state.update({
                "status": "error", "message": "Escribí o pegá un texto para leer.",
            })
            self.push_state()
            return
        if len(text.strip()) > tts.MAX_TEXT_CHARS:
            self._tts_state.update({
                "status": "error", "message": "El texto supera el máximo de 10.000 caracteres.",
            })
            self.push_state()
            return
        if self._tts_cancel_event is not None:
            return
        timer, self._tts_idle_timer = self._tts_idle_timer, None
        if timer is not None:
            timer.cancel()
        voice = str(message.get("voice") or self._tts_preferences["voice"])
        speed = message.get("speed", self._tts_preferences["speed"])
        event = threading.Event()
        self._tts_cancel_event = event
        ready = tts.model_ready(self.data_dir)
        self._tts_state.update({
            "status": "downloading" if not ready else "preparing",
            "model_ready": ready,
            "busy": True,
            "canceling": False,
            "percent": 0 if not ready else 100,
            "message": ("Descargando componentes de voz…" if not ready else
                        "Preparando la voz local…"),
        })
        if self._tts_reader is None:
            self._tts_reader = tts.KokoroReader(self.data_dir, threads=self.cfg.get("threads", 4))
        reader = self._tts_reader

        def work(emit):
            def report(done, total, detail):
                emit({"done": done, "total": total, "message": detail})
            return reader.play(text, voice=voice, speed=speed,
                               cancel_event=event, progress=report)

        def progressed(payload):
            if self._closed:
                return
            detail = payload.get("message") or "Preparando la voz local…"
            status = self._tts_state.get("status", "preparing")
            if str(detail).startswith("Descargando"):
                status = "downloading"
            elif str(detail).startswith("Leyendo"):
                status = "playing"
            elif str(detail).startswith("Componentes de voz verificados"):
                status = "preparing"
            self._tts_state.update({
                "status": status,
                "model_ready": tts.model_ready(self.data_dir),
                "message": detail,
            })
            if isinstance(payload.get("done"), (int, float)) and isinstance(
                    payload.get("total"), (int, float)) and payload["total"]:
                self._tts_state["percent"] = max(0, min(100, int(
                    payload["done"] * 100 / payload["total"])))
            self.push_state()

        def completed(_samples):
            self._tts_cancel_event = None
            if self._closed:
                return
            self._tts_state.update({
                "status": "ready", "model_ready": tts.model_ready(self.data_dir),
                "busy": False, "canceling": False,
                "percent": 100, "message": "Lectura finalizada.",
            })
            self.push_state()
            self._schedule_tts_idle_unload()

        def failed(exc):
            self._tts_cancel_event = None
            if self._closed:
                return
            from instant2_app.models import ModelIntegrityError
            if event.is_set() or type(exc).__name__ == "SpeechCancelled":
                ready = tts.model_ready(self.data_dir)
                status = "ready" if ready else "missing"
                detail = ("Lectura detenida." if ready else
                          "Descarga detenida. El avance se conserva; podés reintentar.")
            elif isinstance(exc, ModelIntegrityError):
                status, detail = "error", (
                    "No se pudieron descargar o verificar los componentes de voz. Revisá la conexión y reintentá."
                )
            elif isinstance(exc, (ImportError, ModuleNotFoundError)):
                status, detail = "error", (
                    "Falta un componente de voz. Reinstalá Instant2 y reintentá."
                )
            elif isinstance(exc, OSError):
                status, detail = "error", (
                    "No se pudo abrir el audio. Revisá el dispositivo de salida."
                )
            else:
                status, detail = "error", (
                    "No se pudo completar la lectura. Revisá el audio y reintentá."
                )
            # No se registra ni se muestra el detalle crudo: un proveedor puede
            # incluir fragmentos del texto en una excepción de síntesis.
            self._tts_state.update({
                "status": status,
                "model_ready": tts.model_ready(self.data_dir),
                "busy": False, "canceling": False,
                "message": detail,
            })
            self.push_state()
            self._schedule_tts_idle_unload()

        self.push_state()
        token = self.tasks.submit(work, completed, failed, progressed)
        if token is None:
            self._tts_cancel_event = None
            self._tts_state.update({"busy": False, "status": "error",
                                    "message": "La lectura no pudo iniciarse."})
            self.push_state()

    def _op_tts_preferences(self, message):
        from instant2_app import tts

        try:
            self._tts_preferences = tts.save_preferences(
                message.get("voice"), message.get("speed"))
        except OSError:
            self.toast("No se guardó la voz",
                       "La lectura puede seguir; revisá los permisos de la configuración.",
                       level="warn")
            return
        self._tts_state.update(self._tts_preferences)
        self.push_state()

    def _op_tts_stop(self, _message):
        if self._tts_cancel_event is None:
            return
        self._tts_state.update({"status": "stopping", "canceling": True,
                                "message": "Deteniendo la lectura…"})
        self._tts_cancel_event.set()
        self.push_state()
