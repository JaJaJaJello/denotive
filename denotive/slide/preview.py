"""Read-only, crisp live rendering of an evidence slide (used by quick look, history and the timeline)."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsView

from .scene import SlideScene


class SlidePreview(QGraphicsView):
    def __init__(self, parent=None, bg="#14161b"):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setInteractive(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(bg))
        self._bg = QColor(bg)
        self.sc = None

    def set_doc(self, doc):
        old = self.sc
        self.sc = SlideScene(doc or {})
        self.sc.rendering = True  # no edit chrome (placeholders, handles)
        self.sc.image_max_dim = 2048
        self.setScene(self.sc)
        if old is not None:
            old.deleteLater()
        QTimer.singleShot(0, self.fit)

    def clear(self):
        self.setScene(None)
        if self.sc is not None:
            self.sc.deleteLater()
        self.sc = None

    def fit(self):
        if self.sc is None:
            return
        r = self.sc.page_rect()
        m = max(r.width(), r.height()) * 0.03
        self.fitInView(r.adjusted(-m, -m, m, m), Qt.KeepAspectRatio)
        self.sc.view_scale = self.transform().m11()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()

    def drawBackground(self, p, rect):
        p.fillRect(rect, self._bg)
        if self.sc is not None:
            p.setPen(Qt.NoPen)
            for page in self.sc.page_rects():
                for i, a in ((2, 50), (6, 26), (12, 12)):
                    p.setBrush(QColor(0, 0, 0, a))
                    p.drawRoundedRect(page.adjusted(-i / 2, -i / 4, i / 2, i), 4, 4)
                p.fillRect(page, QColor(self.sc.bg))

    def drawForeground(self, p, rect):
        pass
