"""Worker delivery without Qt dependencies or periodic polling."""
import queue
import threading
import time

class TaskRunner:
    """Trabajo bloqueante en hilos, resultados encolados para el hilo de UI.

    Los hilos NUNCA tocan la UI: encolan y notifican al dueño; `drain()`
    aplica los resultados en su hilo. `close()` descarta lo que
    llegue después: una ventana cerrada no recibe resultados tardíos.
    """
    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._handlers = {}
        self._pending = 0
        self._closed = False
        self.on_idle = None
        self.on_error = None
        self.notify = None

    def _enqueue(self, kind, token, value):
        with self._lock:
            if self._closed:
                return
            self._queue.put((kind, token, value))
            notify = self.notify
        if notify is not None:
            try:
                notify()
            except RuntimeError:
                # The Qt owner may be destroyed between close() and emission.
                pass

    @property
    def pending(self):
        with self._lock:
            return self._pending

    def submit(self, fn, on_result=None, on_error=None, on_progress=None):
        with self._lock:
            if self._closed:
                return None
            token = id(fn) ^ time.monotonic_ns()
            self._handlers[token] = (on_result, on_error, on_progress)
            self._pending += 1

        def runner():
            try:
                value = fn(lambda payload: self._enqueue("progress", token, payload))
                self._enqueue("result", token, value)
            except Exception as exc:  # noqa: BLE001  (cruza el hilo a la UI)
                self._enqueue("error", token, exc)
            finally:
                self._enqueue("done", token, None)

        threading.Thread(target=runner, daemon=True).start()
        return token

    def drain(self):
        """Aplica los eventos encolados; devuelve cuántos atendió."""
        applied = 0
        while True:
            try:
                kind, token, value = self._queue.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                handlers = self._handlers.get(token)
                closed = self._closed
            if kind == "done":
                with self._lock:
                    self._handlers.pop(token, None)
                    if self._pending:
                        self._pending -= 1
                    idle = self._pending == 0
                if idle and self.on_idle is not None and not closed:
                    self.on_idle()
                continue
            if closed or handlers is None:
                continue
            on_result, on_error, on_progress = handlers
            if kind == "progress" and on_progress is not None:
                on_progress(value)
            elif kind == "result" and on_result is not None:
                on_result(value)
            elif kind == "error":
                if on_error is not None:
                    on_error(value)
                elif self.on_error is not None:
                    self.on_error(value)
            applied += 1
        return applied

    def close(self):
        with self._lock:
            self._closed = True
            self._handlers.clear()
            self._pending = 0
            self.notify = None
            # Release queued results too; they can hold entire synthesis
            # buffers or exception tracebacks after the window is closed.
            self._queue = queue.Queue()
