"""Image import/encoding helpers and an asynchronous, size-bounded pixmap cache for asset images."""
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

KEEP_FORMATS = {"png", "jpg", "jpeg", "gif"}


def image_bytes(img: QImage, fmt="PNG", quality=-1) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, fmt, quality)
    buf.close()
    return bytes(ba.data())


def import_image_file(store, path):
    """Stores an image as an asset (re-encoding formats PowerPoint/PDF handle poorly). Returns (hash, w, h)."""
    path = Path(path)
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        return None
    ext = path.suffix.lower().lstrip(".")
    if ext in KEEP_FORMATS:
        h = store.import_file(path)
    else:
        if img.format() in (QImage.Format_Grayscale16, QImage.Format_RGBA64, QImage.Format_RGBX64):
            img = img.convertToFormat(QImage.Format_ARGB32)
        h = store.import_bytes(image_bytes(img, "PNG"), "png", path.name)
    return h, img.width(), img.height()


def import_qimage(store, img: QImage, name="pasted.png"):
    if img.format() in (QImage.Format_Grayscale16, QImage.Format_RGBA64, QImage.Format_RGBX64):
        img = img.convertToFormat(QImage.Format_ARGB32)
    has_alpha = img.hasAlphaChannel()
    data = image_bytes(img, "PNG")
    if not has_alpha and len(data) > 3_000_000:
        data, ext = image_bytes(img, "JPG", 92), "jpg"
    else:
        ext = "png"
    return store.import_bytes(data, ext, name), img.width(), img.height()


def thumb_asset(store, img: QImage, quality=88):
    return store.import_bytes(image_bytes(img, "JPG", quality), "jpg", "thumb.jpg")


class _Signals(QObject):
    done = Signal(str, str, QImage)


class _Job(QRunnable):
    def __init__(self, h, key, path, max_dim, sig):
        super().__init__()
        self.h, self.key, self.path, self.max_dim, self.sig = h, key, path, max_dim, sig

    def run(self):
        img = load_image(self.path, self.max_dim)
        self.sig.done.emit(self.h, self.key, img)


def load_image(path, max_dim=0) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if max_dim and size.isValid() and max(size.width(), size.height()) > max_dim:
        s = max_dim / max(size.width(), size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * s)), max(1, round(size.height() * s))))
    img = reader.read()
    if img.format() in (QImage.Format_Grayscale16, QImage.Format_RGBA64, QImage.Format_RGBX64):
        img = img.convertToFormat(QImage.Format_ARGB32)
    return img


class ImageCache(QObject):
    """Pixmaps keyed by (asset hash, max dimension). Loads off the UI thread; LRU-evicts past a byte budget."""
    loaded = Signal(str)

    def __init__(self, store, budget_mb=420):
        super().__init__()
        self.store = store
        self.budget = budget_mb * 1024 * 1024
        self.used = 0
        self._pix = OrderedDict()
        self._pending = set()
        self._missing = set()
        self._sig = _Signals()
        self._sig.done.connect(self._on_done)
        self.pool = QThreadPool.globalInstance()

    def _put(self, key, pm):
        old = self._pix.pop(key, None)
        if old is not None:
            self.used -= old.width() * old.height() * 4
        self._pix[key] = pm
        self.used += pm.width() * pm.height() * 4
        while self.used > self.budget and len(self._pix) > 1:
            _, ev = self._pix.popitem(last=False)
            self.used -= ev.width() * ev.height() * 4

    def peek(self, h, max_dim=1024):
        key = f"{h}@{max_dim}"
        pm = self._pix.get(key)
        if pm is not None:
            self._pix.move_to_end(key)
        return pm

    def get(self, h, max_dim=1024):
        """Returns the pixmap if ready, otherwise schedules a background load and returns None."""
        if not h or h in self._missing:
            return None
        key = f"{h}@{max_dim}"
        pm = self._pix.get(key)
        if pm is not None:
            self._pix.move_to_end(key)
            return pm
        if key not in self._pending:
            path = self.store.asset_path(h)
            if path is None or not path.exists():
                self._missing.add(h)
                return None
            self._pending.add(key)
            self.pool.start(_Job(h, key, path, max_dim, self._sig))
        return None

    def get_sync(self, h, max_dim=0):
        key = f"{h}@{max_dim}"
        pm = self._pix.get(key)
        if pm is not None:
            self._pix.move_to_end(key)
            return pm
        path = self.store.asset_path(h)
        if path is None or not path.exists():
            return QPixmap()
        pm = QPixmap.fromImage(load_image(path, max_dim))
        if not pm.isNull():
            self._put(key, pm)
        return pm

    def _on_done(self, h, key, img):
        self._pending.discard(key)
        if img.isNull():
            self._missing.add(h)
            return
        self._put(key, QPixmap.fromImage(img))
        self.loaded.emit(h)
