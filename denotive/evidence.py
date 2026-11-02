"""Higher-level operations that create or update evidence and attached files in the store."""
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF

from . import context, media, textextract
from .settings import settings

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}
NO_SNAPSHOT = {"exe", "lnk", "url", "msi", "dll", "sys"}


def image_doc(asset, iw, ih, name="", page=(1280, 720)):
    pw, ph = page
    s = min(pw * 0.94 / max(1, iw), ph * 0.94 / max(1, ih))
    w, h = iw * s, ih * s
    return {"v": 1, "w": pw, "h": ph, "bg": "#ffffff", "notes": "",
            "items": [{"id": "img", "type": "image", "x": (pw - w) / 2, "y": (ph - h) / 2, "w": w, "h": h,
                       "z": 1, "asset": asset, "iw": iw, "ih": ih, "crop": [0, 0, iw, ih], "name": name}]}


def text_doc(text, page=(1280, 720)):
    pw, ph = page
    return {"v": 1, "w": pw, "h": ph, "bg": "#ffffff", "notes": "",
            "items": [{"id": "txt", "type": "text", "x": 80, "y": 70, "w": pw - 160, "h": 40, "z": 1,
                       "plain": text, "size": 28, "font": "Segoe UI", "color": "#1d1f24"}]}


def render_doc_thumb(doc, width=960):
    from .slide.scene import SlideScene
    sc = SlideScene(doc)
    img = sc.render_image(width)
    text = sc.plain_text()
    sc.deleteLater()
    return img, text


def new_evidence(board_id, center: QPointF, title, doc):
    from .board.items import evidence_card_height
    st = context.store
    img, text = render_doc_thumb(doc)
    thumb = media.thumb_asset(st, img)
    from .slide.items import doc_aspect
    aspect = doc_aspect(doc)
    w = 300.0 if doc.get("h", 720) / doc.get("w", 1280) <= 1.05 else 230.0
    h = evidence_card_height(w, aspect)
    row = st.new_item(board_id, "evidence", center.x() - w / 2, center.y() - h / 2, w, h, {"aspect": aspect})
    with st.tx():
        st.put_item(dict(row, deleted=None))
        st.add_version(row["id"], title, doc, text, thumb)
    return row["id"]


def evidence_from_image_file(board_id, center, path):
    r = media.import_image_file(context.store, path)
    if not r:
        return None
    h, iw, ih = r
    return new_evidence(board_id, center, Path(path).stem, image_doc(h, iw, ih, Path(path).name))


def evidence_from_qimage(board_id, center, img, title):
    h, iw, ih = media.import_qimage(context.store, img)
    return new_evidence(board_id, center, title, image_doc(h, iw, ih, title))


def _file_payload(p: Path):
    st = context.store
    is_file = p.is_file()
    stat = p.stat() if p.exists() else None
    size = stat.st_size if (stat and is_file) else None
    mtime = stat.st_mtime if stat else None
    ext = p.suffix.lower().lstrip(".")
    text = textextract.extract(p) if is_file and (size or 0) < 300_000_000 else ""
    preview = textextract.preview(text) if text else ""
    limit = float(settings.get("snapshot_limit_mb", 50)) * 1024 * 1024
    asset = None
    if is_file and ext not in NO_SNAPSHOT and (size or 0) <= limit:
        try:
            asset = st.import_file(p)
        except OSError:
            asset = None
    thumb = None
    if is_file and ext in IMAGE_EXTS:
        img = media.load_image(p, 640)
        if not img.isNull():
            thumb = st.import_bytes(media.image_bytes(img, "PNG"), "png", "thumb.png")
    doc = {"asset": asset, "size": size, "mtime": mtime, "preview": preview, "path": str(p)}
    return doc, text, thumb


def attach_file(board_id, center: QPointF, path):
    st = context.store
    p = Path(path)
    doc, text, thumb = _file_payload(p)
    ext = p.suffix.lower().lstrip(".") if p.is_file() else ""
    big = bool(doc["preview"] or thumb)
    w, h = (330.0, 240.0) if big else (300.0, 132.0)
    data = {"name": p.name, "path": str(p), "ext": ext, "size": doc["size"], "mtime": doc["mtime"],
            "thumb": thumb, "folder": p.is_dir()}
    row = st.new_item(board_id, "file", center.x() - w / 2, center.y() - h / 2, w, h, data)
    with st.tx():
        st.put_item(row)
        st.add_version(row["id"], p.name, doc, f"{p.name}\n{p}\n{text}")
    return row["id"]


def update_file_snapshot(item_id):
    """Re-reads the original file. Stacks a new version if it changed. Returns 'updated', 'same' or 'missing'."""
    st = context.store
    it = st.item(item_id)
    if it is None:
        return "missing"
    p = Path(it["data"].get("path") or "")
    if not p.exists():
        return "missing"
    cur = st.current_version(item_id)
    doc, text, thumb = _file_payload(p)
    if cur and cur["doc"].get("asset") == doc["asset"] and cur["doc"].get("mtime") == doc["mtime"]:
        return "same"
    st.add_version(item_id, p.name, doc, f"{p.name}\n{p}\n{text}")
    it = st.item(item_id)
    it["data"].update(size=doc["size"], mtime=doc["mtime"], thumb=thumb or it["data"].get("thumb"))
    st.put_item(it)
    return "updated"


def snapshot_copy(item_id, version=None):
    """Copies a stored snapshot to a temp folder under its original name and returns that path."""
    st = context.store
    v = version or st.current_version(item_id)
    it = st.item(item_id)
    if not v or not it:
        return None
    src = st.asset_path(v["doc"].get("asset"))
    if not src or not src.exists():
        return None
    d = Path(tempfile.gettempdir()) / "DeNotive" / f"{item_id[:8]}-v{v['num']}"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / (it["data"].get("name") or src.name)
    if not dest.exists():
        shutil.copyfile(src, dest)
    return dest


def open_path(path):
    try:
        os.startfile(str(path))
        return True
    except OSError:
        return False


def reveal(path):
    import subprocess
    p = Path(path)
    if p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
        return True
    if p.parent.exists():
        os.startfile(str(p.parent))
        return True
    return False
