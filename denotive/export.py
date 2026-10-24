"""Exporting boards. Every evidence card becomes one slide/page; an optional first page shows the whole board.

PDF and print are vector (text stays text). PowerPoint export rebuilds slides with native, editable shapes
(text boxes, pictures with crops, autoshapes, connectors, freeform ink); anything without a clean equivalent
(tables, rounded image corners, rotated ink) is placed as a picture of just that object.
"""
import io
from datetime import datetime

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter,
                           QTextDocument, QTextFormat, QTextListFormat)

from . import context, media, theme
from .board.view import BG_COLORS, cork_tile

SLIDE_W_EMU = 12192000
SLIDE_H_EMU = 6858000


# ---------------------------------------------------------------------- gathering
def reading_order(rows):
    """Top-to-bottom rows, left-to-right within a row (cards whose vertical extents overlap share a row)."""
    rows = sorted(rows, key=lambda r: r["y"])
    bands = []
    for r in rows:
        mid = r["y"] + r["h"] / 2
        for band in bands:
            if band["top"] <= mid <= band["bottom"]:
                band["items"].append(r)
                band["bottom"] = max(band["bottom"], r["y"] + r["h"])
                break
        else:
            bands.append({"top": r["y"], "bottom": r["y"] + r["h"], "items": [r]})
    out = []
    for band in bands:
        out += sorted(band["items"], key=lambda r: r["x"])
    return out


def board_pages(board_id, order="position", only_ids=None):
    st = context.store
    items = st.items(board_id)
    by_id = {i["id"]: i for i in items}
    ev = [i for i in items if i["kind"] == "evidence" and (not only_ids or i["id"] in only_ids)]
    if order == "created":
        ev.sort(key=lambda r: r["created"] or 0)
    elif order == "updated":
        ev.sort(key=lambda r: r["updated"] or 0)
    else:
        ev = reading_order(ev)
    links = {}
    for c in st.connections(board_id):
        a, b = by_id.get(c["src"]), by_id.get(c["dst"])
        if not a or not b:
            continue
        for me, other, arrow in ((a, b, "→"), (b, a, "←")):
            if other["kind"] == "boardlink":
                ob = st.board(other["data"].get("board_id"))
                name = f"Board: {ob['name']}" if ob else "Board"
            else:
                name = other.get("title") or other["data"].get("text") or other["data"].get("title") or other["kind"]
            name = (name.splitlines() or [""])[0][:80]
            links.setdefault(me["id"], []).append(f"{arrow} {name}" + (f"  “{c['label']}”" if c.get("label") else ""))
    pages = []
    for r in ev:
        v = st.current_version(r["id"])
        if v is None:
            continue
        pages.append({"item": r, "version": v, "doc": v["doc"], "title": v["title"] or r.get("title") or "",
                      "links": links.get(r["id"], [])})
    return pages


def page_notes(pg):
    parts = []
    notes = pg["doc"].get("notes", "").strip()
    if notes:
        parts.append(notes)
    if pg["links"]:
        parts.append("Connections:\n" + "\n".join(pg["links"]))
    v = pg["version"]
    parts.append(f"Evidence v{v['num']} · updated {datetime.fromtimestamp(v['created']):%Y-%m-%d %H:%M}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- board overview
def _paint_board_bg(p, rect, background):
    p.fillRect(rect, QColor(BG_COLORS.get(background, "#0f1217")))
    if background == "cork":
        p.fillRect(rect, QBrush(cork_tile()))


def render_board_image(scene, background="midnight", max_px=3000, transparent=False):
    r = scene.content_rect()
    if r.isNull():
        r = QRectF(-400, -300, 800, 600)
    r = r.adjusted(-70, -70, 70, 70)
    s = min(max_px / max(r.width(), r.height()), 2.5)
    img = QImage(max(1, int(r.width() * s)), max(1, int(r.height() * s)), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    if not transparent:
        p.save()
        p.scale(s, s)
        p.translate(-r.topLeft())
        _paint_board_bg(p, r, background)
        p.restore()
    render_board(scene, p, QRectF(0, 0, img.width(), img.height()), r)
    p.end()
    return img


def render_board(scene, painter, target, source):
    sel = scene.selectedItems()
    scene.clearSelection()
    scene.export_mode = True
    scene.render(painter, target, source, Qt.KeepAspectRatio)
    scene.export_mode = False
    for it in sel:
        it.setSelected(True)


def _scene_for(board_id):
    from .board.scene import BoardScene
    b = context.store.board(board_id)
    return BoardScene(b), b


def _paint_overview(p, page_rect, board, scene, project):
    """Title band + the board itself, fitted below it."""
    bg = QColor("#101216")
    p.fillRect(page_rect, bg)
    k = page_rect.width() / 1280.0
    band = QRectF(page_rect.x(), page_rect.y(), page_rect.width(), 92 * k)
    p.setPen(QColor(theme.ACCENT))
    f = QFont(theme.UI_FONT)
    f.setPixelSize(max(1, int(13 * k)))
    f.setBold(True)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5 * k)
    p.setFont(f)
    p.drawText(QRectF(band.x() + 40 * k, band.y() + 18 * k, band.width(), 20 * k), Qt.AlignLeft | Qt.AlignVCenter,
               (project["name"] if project else "").upper())
    f2 = QFont(theme.UI_FONT)
    f2.setPixelSize(max(1, int(32 * k)))
    f2.setBold(True)
    p.setFont(f2)
    p.setPen(QColor("#f2f3f5"))
    p.drawText(QRectF(band.x() + 40 * k, band.y() + 38 * k, band.width() - 80 * k, 44 * k),
               Qt.AlignLeft | Qt.AlignVCenter, board["name"])
    f3 = QFont(theme.UI_FONT)
    f3.setPixelSize(max(1, int(13 * k)))
    p.setFont(f3)
    p.setPen(QColor("#8c919d"))
    n_ev = sum(1 for i in scene.items_by_id.values() if i.KIND == "evidence")
    p.drawText(QRectF(band.x(), band.y() + 38 * k, band.width() - 40 * k, 44 * k), Qt.AlignRight | Qt.AlignVCenter,
               f"{n_ev} evidence · {len(scene.strings_by_id)} strings · {datetime.now():%b %d, %Y}")
    area = QRectF(page_rect.x() + 30 * k, band.bottom() + 6 * k, page_rect.width() - 60 * k,
                  page_rect.height() - band.height() - 36 * k)
    src = scene.content_rect()
    if src.isNull():
        return
    src = src.adjusted(-60, -60, 60, 60)
    s = min(area.width() / src.width(), area.height() / src.height())
    tgt = QRectF(0, 0, src.width() * s, src.height() * s)
    tgt.moveCenter(area.center())
    p.save()
    clip = QRectF(tgt)
    p.setClipRect(clip)
    p.translate(tgt.topLeft())
    p.scale(s, s)
    p.translate(-src.topLeft())
    _paint_board_bg(p, src, board.get("background") or "midnight")
    p.restore()
    render_board(scene, p, tgt, src)


# ---------------------------------------------------------------------- PDF / print
def _slide_into(painter, doc, target):
    from .slide.scene import SlideScene
    sc = SlideScene(doc)
    sc.render_into(None, target, painter=painter)
    sc.deleteLater()


def _fit(rect, w, h):
    s = min(rect.width() / w, rect.height() / h)
    r = QRectF(0, 0, w * s, h * s)
    r.moveCenter(rect.center())
    return r


def export_pdf(path, board_ids, overview=True, order="position", only_ids=None, progress=None):
    st = context.store
    writer = QPdfWriter(str(path))
    writer.setResolution(240)
    writer.setCreator("DeNotive")
    first_board = st.board(board_ids[0])
    writer.setTitle(first_board["name"] if len(board_ids) == 1 else "DeNotive export")
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    wide = QPageSize(QSizeF(13.3333, 7.5), QPageSize.Inch, "Widescreen")
    writer.setPageSize(wide)
    painter = None
    first = True
    count = 0
    for bid in board_ids:
        scene, board = _scene_for(bid)
        project = st.project(board["project_id"])
        pages = board_pages(bid, order, only_ids)
        jobs = ([("overview", None)] if overview else []) + [("slide", pg) for pg in pages]
        for kind, pg in jobs:
            if kind == "overview":
                size = wide
            else:
                pw, ph = pg["doc"].get("w", 1280), pg["doc"].get("h", 720)
                size = QPageSize(QSizeF(pw / 96, ph / 96), QPageSize.Inch, "Slide")
            if first:
                writer.setPageSize(size)
                writer.setPageMargins(QMarginsF(0, 0, 0, 0))
                painter = QPainter(writer)
                first = False
            else:
                writer.setPageSize(size)
                writer.setPageMargins(QMarginsF(0, 0, 0, 0))
                writer.newPage()
            rect = QRectF(0, 0, writer.width(), writer.height())
            if kind == "overview":
                _paint_overview(painter, rect, board, scene, project)
            else:
                _slide_into(painter, pg["doc"], rect)
            count += 1
            if progress:
                progress(count)
        scene.teardown()
        scene.deleteLater()
    if painter is None:
        painter = QPainter(writer)
        painter.drawText(QRectF(0, 0, writer.width(), writer.height()), Qt.AlignCenter, "No evidence to export")
    painter.end()
    return count


def print_boards(printer, board_ids, overview=True, order="position", only_ids=None):
    st = context.store
    printer.setPageOrientation(QPageLayout.Landscape)
    painter = QPainter(printer)
    first = True
    for bid in board_ids:
        scene, board = _scene_for(bid)
        project = st.project(board["project_id"])
        pages = board_pages(bid, order, only_ids)
        jobs = ([("overview", None)] if overview else []) + [("slide", pg) for pg in pages]
        for kind, pg in jobs:
            if not first:
                printer.newPage()
            first = False
            page = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
            page.moveTo(0, 0)
            if kind == "overview":
                _paint_overview(painter, _fit(page, 1280, 720), board, scene, project)
            else:
                pw, ph = pg["doc"].get("w", 1280), pg["doc"].get("h", 720)
                _slide_into(painter, pg["doc"], _fit(page, pw, ph))
        scene.teardown()
        scene.deleteLater()
    painter.end()


# ---------------------------------------------------------------------- PowerPoint
def _render_item_png(d, doc, scale=2.0):
    """Picture of a single slide object (transparent background) and its slide-space rect."""
    from .slide.scene import SlideScene
    sc = SlideScene({"w": doc.get("w", 1280), "h": doc.get("h", 720), "bg": doc.get("bg", "#ffffff"),
                     "items": [dict(d, hidden=False)]})
    sc.transparent = True
    its = sc.slide_items()
    if not its:
        return None, None
    it = its[0]
    br = it.sceneBoundingRect()
    img = QImage(max(1, int(br.width() * scale)), max(1, int(br.height() * scale)),
                 QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    sc.rendering = True
    sc.render(p, QRectF(0, 0, img.width(), img.height()), br, Qt.IgnoreAspectRatio)
    sc.rendering = False
    p.end()
    sc.deleteLater()
    return media.image_bytes(img, "PNG"), br


class _PptxBuilder:
    def __init__(self, editable=True):
        from pptx import Presentation
        from pptx.util import Emu
        self.Emu = Emu
        self.prs = Presentation()
        self.prs.slide_width = Emu(SLIDE_W_EMU)
        self.prs.slide_height = Emu(SLIDE_H_EMU)
        self.blank = self.prs.slide_layouts[6]
        self.editable = editable

    # coordinate mapping for the current slide
    def _setup(self, doc):
        pw, ph = float(doc.get("w", 1280)), float(doc.get("h", 720))
        self.f = min(SLIDE_W_EMU / pw, SLIDE_H_EMU / ph)
        self.ox = (SLIDE_W_EMU - pw * self.f) / 2
        self.oy = (SLIDE_H_EMU - ph * self.f) / 2
        self.pt_k = self.f / 9525.0

    def X(self, v):
        return self.Emu(int(round(self.ox + v * self.f)))

    def Y(self, v):
        return self.Emu(int(round(self.oy + v * self.f)))

    def L(self, v):
        return self.Emu(max(1, int(round(v * self.f))))

    @staticmethod
    def rgb(c):
        from pptx.dml.color import RGBColor
        q = QColor(c)
        return RGBColor(q.red(), q.green(), q.blue())

    def _bg(self, slide, color):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = self.rgb(color)

    def add_overview(self, board, scene, project):
        slide = self.prs.slides.add_slide(self.blank)
        img = QImage(1920, 1080, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        _paint_overview(p, QRectF(0, 0, 1920, 1080), board, scene, project)
        p.end()
        slide.shapes.add_picture(io.BytesIO(media.image_bytes(img, "PNG")), 0, 0, self.Emu(SLIDE_W_EMU),
                                 self.Emu(SLIDE_H_EMU))
        slide.notes_slide.notes_text_frame.text = f"Board overview: {board['name']}"

    def add_page(self, pg):
        doc = pg["doc"]
        self._setup(doc)
        slide = self.prs.slides.add_slide(self.blank)
        bg = doc.get("bg", "#ffffff")
        self._bg(slide, bg)
        if not self.editable:
            from .slide.scene import SlideScene
            sc = SlideScene(doc)
            png = media.image_bytes(sc.render_image(1920), "PNG")
            sc.deleteLater()
            pw, ph = doc.get("w", 1280), doc.get("h", 720)
            slide.shapes.add_picture(io.BytesIO(png), self.X(0), self.Y(0), self.L(pw), self.L(ph))
        else:
            for d in sorted(doc.get("items", []), key=lambda d: d.get("z", 0)):
                if d.get("hidden"):
                    continue
                try:
                    ok = self._native(slide, d)
                except Exception:
                    ok = False
                if not ok:
                    self._picture_of(slide, d, doc)
        slide.notes_slide.notes_text_frame.text = page_notes(pg)

    def _picture_of(self, slide, d, doc):
        png, br = _render_item_png(d, doc)
        if png:
            slide.shapes.add_picture(io.BytesIO(png), self.X(br.x()), self.Y(br.y()), self.L(br.width()),
                                     self.L(br.height()))

    def _native(self, slide, d):
        t = d.get("type")
        if t == "text":
            if "<table" in (d.get("html") or ""):
                return False
            return self._text_box(slide, d)
        if t == "shape":
            return self._shape(slide, d)
        if t == "image":
            return self._image(slide, d)
        if t == "line":
            return self._line(slide, d)
        if t == "stroke":
            return self._ink(slide, d)
        return False

    def _line_style(self, line, color, width, dash="solid", alpha=None):
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        line.color.rgb = self.rgb(color)
        line.width = self.L(width)
        if dash and dash != "solid":
            line.dash_style = {"dash": MSO_LINE_DASH_STYLE.DASH, "dot": MSO_LINE_DASH_STYLE.ROUND_DOT,
                               "dashdot": MSO_LINE_DASH_STYLE.DASH_DOT}.get(dash, MSO_LINE_DASH_STYLE.SOLID)
        if alpha is not None:
            from pptx.oxml.ns import qn
            ln = line._get_or_add_ln()
            clr = ln.find(qn("a:solidFill")).find(qn("a:srgbClr"))
            a = clr.makeelement(qn("a:alpha"), {"val": str(int(alpha * 100000))})
            clr.append(a)

    def _fill_text(self, tf, d, center=False):
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Pt
        doc = QTextDocument()
        f = QFont(d.get("font") or "Segoe UI")
        f.setPixelSize(int(d.get("size", 24)))
        doc.setDefaultFont(f)
        doc.setHtml(d.get("html") or "")
        if d.get("plain") and not d.get("html"):
            doc.setPlainText(d["plain"])
        default_color = d.get("color", "#1d1f24")
        tf.word_wrap = True
        block = doc.begin()
        first = True
        while block.isValid():
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            a = block.blockFormat().alignment()
            if center and not block.blockFormat().hasProperty(QTextFormat.BlockAlignment):
                p.alignment = PP_ALIGN.CENTER
            elif a & Qt.AlignHCenter:
                p.alignment = PP_ALIGN.CENTER
            elif a & Qt.AlignRight:
                p.alignment = PP_ALIGN.RIGHT
            elif a & Qt.AlignJustify:
                p.alignment = PP_ALIGN.JUSTIFY
            lst = block.textList()
            prefix = ""
            if lst is not None:
                style = lst.format().style()
                numbered = style in (QTextListFormat.ListDecimal, QTextListFormat.ListLowerAlpha,
                                     QTextListFormat.ListUpperAlpha, QTextListFormat.ListLowerRoman,
                                     QTextListFormat.ListUpperRoman)
                prefix = f"{lst.itemNumber(block) + 1}. " if numbered else "• "
            it = block.begin()
            first_run = True
            while not it.atEnd():
                frag = it.fragment()
                txt = frag.text()
                cf = frag.charFormat()
                for k, piece in enumerate(txt.split(" ")):
                    if k > 0:
                        p.add_line_break()
                    if not piece and not (first_run and prefix):
                        continue
                    run = p.add_run()
                    run.text = (prefix if first_run else "") + piece.replace("￼", "")
                    first_run = False
                    font = run.font
                    from .slide.editor import fmt_px
                    px = fmt_px(cf, d.get("size", 24))
                    font.size = Pt(max(1.0, px * 0.75 * self.pt_k))
                    fams = cf.fontFamilies() if cf.hasProperty(QTextFormat.FontFamilies) else None
                    font.name = (fams[0] if fams else None) or d.get("font") or "Segoe UI"
                    font.bold = cf.fontWeight() >= QFont.DemiBold
                    font.italic = cf.fontItalic()
                    font.underline = cf.fontUnderline()
                    col = cf.foreground().color() if cf.hasProperty(QTextFormat.ForegroundBrush) \
                        else QColor(default_color)
                    font.color.rgb = self.rgb(col.name())
                it += 1
            if first_run and prefix:
                run = p.add_run()
                run.text = prefix
            block = block.next()
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE if center else MSO_ANCHOR.TOP

    def _text_box(self, slide, d):
        from pptx.enum.text import MSO_AUTO_SIZE
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        shp = slide.shapes.add_textbox(self.X(x), self.Y(y), self.L(w), self.L(h))
        tf = shp.text_frame
        tf.auto_size = MSO_AUTO_SIZE.NONE
        pad = float(d.get("pad", 6)) + 2
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = self.L(pad)
        if d.get("fill"):
            shp.fill.solid()
            shp.fill.fore_color.rgb = self.rgb(d["fill"])
        if d.get("stroke") and float(d.get("sw", 0)) > 0:
            self._line_style(shp.line, d["stroke"], float(d["sw"]))
        if d.get("rot"):
            shp.rotation = float(d["rot"])
        self._fill_text(tf, d)
        return True

    SHAPE_MAP = {"rect": "RECTANGLE", "round": "ROUNDED_RECTANGLE", "ellipse": "OVAL",
                 "triangle": "ISOSCELES_TRIANGLE", "diamond": "DIAMOND", "hexagon": "HEXAGON",
                 "parallelogram": "PARALLELOGRAM", "star": "STAR_5_POINT", "arrow": "RIGHT_ARROW",
                 "chevron": "CHEVRON", "callout": "ROUNDED_RECTANGULAR_CALLOUT", "cross": "CROSS"}

    def _shape(self, slide, d):
        from pptx.enum.shapes import MSO_SHAPE
        kind = getattr(MSO_SHAPE, self.SHAPE_MAP.get(d.get("shape", "rect"), "RECTANGLE"))
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        shp = slide.shapes.add_shape(kind, self.X(x), self.Y(y), self.L(w), self.L(h))
        if d.get("shape") == "round":
            try:
                shp.adjustments[0] = max(0.0, min(0.5, float(d.get("radius", 16)) / max(1.0, min(w, h))))
            except (IndexError, ValueError):
                pass
        if d.get("fill"):
            shp.fill.solid()
            shp.fill.fore_color.rgb = self.rgb(d["fill"])
        else:
            shp.fill.background()
        if d.get("stroke") and float(d.get("sw", 0)) > 0:
            self._line_style(shp.line, d["stroke"], float(d["sw"]), d.get("dash", "solid"))
        else:
            shp.line.fill.background()
        if not d.get("shadow"):
            shp.shadow.inherit = False
        if d.get("rot"):
            shp.rotation = float(d["rot"])
        if d.get("html") or d.get("plain"):
            self._fill_text(shp.text_frame, d, center=True)
        return True

    def _image(self, slide, d):
        if float(d.get("radius", 0)) > 0.5:
            return False
        path = context.store.asset_path(d.get("asset"))
        if path is None or not path.exists():
            return False
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        pic = slide.shapes.add_picture(str(path), self.X(x), self.Y(y), self.L(w), self.L(h))
        iw, ih = float(d.get("iw", 1)), float(d.get("ih", 1))
        cx, cy, cw, ch = d.get("crop") or [0, 0, iw, ih]
        if (cx, cy, cw, ch) != (0, 0, iw, ih):
            pic.crop_left = cx / iw
            pic.crop_top = cy / ih
            pic.crop_right = max(0.0, 1 - (cx + cw) / iw)
            pic.crop_bottom = max(0.0, 1 - (cy + ch) / ih)
        if d.get("stroke") and float(d.get("sw", 0)) > 0:
            self._line_style(pic.line, d["stroke"], float(d["sw"]))
        if d.get("rot"):
            pic.rotation = float(d["rot"])
        return True

    def _line(self, slide, d):
        from pptx.enum.shapes import MSO_CONNECTOR
        from pptx.oxml.ns import qn
        x, y = d["x"], d["y"]
        x1, y1, x2, y2 = x + d["x1"], y + d["y1"], x + d["x2"], y + d["y2"]
        con = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, self.X(x1), self.Y(y1), self.X(x2), self.Y(y2))
        self._line_style(con.line, d.get("stroke", "#1d1f24"), float(d.get("sw", 4)), d.get("dash", "solid"))
        ln = con.line._get_or_add_ln()
        if d.get("a1"):
            ln.append(ln.makeelement(qn("a:headEnd"), {"type": "triangle", "w": "med", "len": "med"}))
        if d.get("a2"):
            ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
        return True

    def _ink(self, slide, d):
        if abs(float(d.get("rot", 0))) > 0.01:
            return False
        pts = d.get("pts") or []
        if len(pts) < 2:
            return False
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        abs_pts = [(self.ox + (x + u * w) * self.f, self.oy + (y + v * h) * self.f) for u, v in pts]
        b = slide.shapes.build_freeform(int(abs_pts[0][0]), int(abs_pts[0][1]), scale=1.0)
        b.add_line_segments([(int(px), int(py)) for px, py in abs_pts[1:]], close=False)
        shp = b.convert_to_shape()
        shp.fill.background()
        self._line_style(shp.line, d.get("color", "#e5484d"), float(d.get("sw", 4)),
                         alpha=0.38 if d.get("hl") else None)
        shp.shadow.inherit = False
        return True

    def save(self, path):
        self.prs.save(str(path))


def export_pptx(path, board_ids, overview=True, order="position", only_ids=None, editable=True, progress=None):
    st = context.store
    pb = _PptxBuilder(editable)
    count = 0
    for bid in board_ids:
        scene, board = _scene_for(bid)
        project = st.project(board["project_id"])
        if overview:
            pb.add_overview(board, scene, project)
            count += 1
        for pg in board_pages(bid, order, only_ids):
            pb.add_page(pg)
            count += 1
            if progress:
                progress(count)
        scene.teardown()
        scene.deleteLater()
    pb.save(path)
    return count
