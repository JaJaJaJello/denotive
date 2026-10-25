"""Runs a provider on a background thread and reports back through Qt signals."""
import base64
import threading

from PySide6.QtCore import QObject, Signal

from .providers import AIError, NeedsLogin, make


class Run(QObject):
    chunk = Signal(str)
    done = Signal(str)
    failed = Signal(str, bool)  # message, needs sign-in

    def __init__(self, cfg, system, messages, max_tokens=None):
        super().__init__()
        self.cfg, self.system, self.messages, self.max_tokens = cfg, system, messages, max_tokens
        self._stop = False
        self.provider = None

    def start(self):
        threading.Thread(target=self._go, daemon=True).start()

    def stop(self):
        self._stop = True

    def _go(self):
        try:
            self.provider = make(self.cfg)
            kw = {"max_tokens": self.max_tokens} if self.max_tokens else {}
            text = self.provider.stream(self.system, self.messages, self.chunk.emit, lambda: self._stop, **kw)
            self.done.emit(text)
        except NeedsLogin as e:
            self.failed.emit(str(e), True)
        except AIError as e:
            self.failed.emit(str(e), False)
        except Exception as e:  # anything unexpected still lands in the UI instead of killing the thread
            self.failed.emit(f"{type(e).__name__}: {e}", False)


def qimage_b64(img, max_side=1280, quality=85):
    """JPEG-encodes a QImage for sending to a vision model."""
    from ..media import image_bytes
    if max(img.width(), img.height()) > max_side:
        from PySide6.QtCore import Qt
        img = img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return "image/jpeg", base64.b64encode(image_bytes(img, "JPG", quality)).decode()
