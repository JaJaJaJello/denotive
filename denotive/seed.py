"""First-run sample content: a small board that shows off (and explains) the main ideas."""

from PySide6.QtCore import QPointF

from . import context, evidence


def _slide(title, lines, accent="#e5484d", extra=None):
    items = [
        {"id": "bar", "type": "shape", "shape": "rect", "x": 0, "y": 0, "w": 1280, "h": 14, "fill": accent, "z": 1},
        {"id": "t", "type": "text", "x": 72, "y": 70, "w": 1100, "h": 60, "z": 2, "plain": title, "size": 56,
         "font": "Segoe UI", "color": "#1d1f26",
         "html": f"<p style='font-weight:700'>{title}</p>"},
        {"id": "b", "type": "text", "x": 76, "y": 190, "w": 700, "h": 300, "z": 3, "size": 28, "font": "Segoe UI",
         "color": "#3a3d46", "html": "".join(f"<p style='margin-bottom:14px'>• {ln}</p>" for ln in lines)},
    ]
    for d in extra or []:
        items.append(d)
    return {"v": 1, "w": 1280, "h": 720, "bg": "#ffffff", "items": items, "notes": ""}


def create_welcome():
    st = context.store
    p = st.create_project("Welcome to DeNotive", "#e5484d")
    b = st.create_board(p["id"], "Start here", "midnight")
    bid = b["id"]
    shapes = [
        {"id": "s1", "type": "shape", "shape": "round", "x": 860, "y": 210, "w": 330, "h": 120, "z": 4,
         "fill": "#5b8cff", "color": "#ffffff", "size": 30, "radius": 18, "html": "<p align='center'><b>Evidence</b></p>",
         "shadow": True},
        {"id": "s2", "type": "shape", "shape": "round", "x": 860, "y": 470, "w": 330, "h": 120, "z": 5,
         "fill": "#e5484d", "color": "#ffffff", "size": 30, "radius": 18, "html": "<p align='center'><b>Insight</b></p>",
         "shadow": True},
        {"id": "a1", "type": "line", "x": 1025, "y": 340, "x1": 0, "y1": 0, "x2": 0, "y2": 118, "stroke": "#1d1f26",
         "sw": 5, "a2": True, "z": 6},
    ]
    v1 = _slide("Welcome, detective", [
        "Every card on a board is a slide you build like PowerPoint.",
        "Text, pictures, shapes, arrows, ink, crops, layers.",
        "Double-click a card to edit it."], extra=shapes)
    e1 = evidence.new_evidence(bid, QPointF(-250, -60), "Welcome, detective", v1)
    v2 = _slide("Welcome, detective", [
        "Every card on a board is a slide you build like PowerPoint.",
        "Editing stacks a new version on top, like a fresh post-it.",
        "Older versions peek out underneath. Open History to go back."], extra=shapes)
    v2["notes"] = "This card has two versions. Right-click it → Version history to compare them."
    from .slide.scene import SlideScene
    sc = SlideScene(v2)
    from . import media
    st.add_version(e1, "Welcome, detective", v2, sc.plain_text(), media.thumb_asset(st, sc.render_image(960)))
    sc.deleteLater()
    v3 = _slide("Tie it together", [
        "Drag from a red pin to another card to tie a string.",
        "Double-click a string to label it (e.g. “grows with T”).",
        "Colour, dash and arrow styles live in the side panel."], accent="#f5b544")
    e2 = evidence.new_evidence(bid, QPointF(180, 300), "Tie it together", v3)
    notes = [
        (-680, -150, "Right-click the board →\nAdd evidence", "#fff09a"),
        (-680, 150, "Drop scripts, data files or programs onto the board. DeNotive keeps a snapshot of the exact "
                    "file you used.", "#a8d4ff"),
        (470, -150, "Ctrl+K searches everything — slide text, notes, string labels and file contents.", "#b8f0cf"),
        (470, 250, "Timeline (left) lists all evidence by date — great for a weekly check-in.", "#ffcf8a"),
    ]
    rows = []
    for x, y, text, fill in notes:
        rows.append(st.new_item(bid, "sticky", x, y, 230, 200, {"text": text, "fill": fill, "size": 17,
                                                                 "font": "Segoe Print"}))
    rows.append(st.new_item(bid, "note", -680, -275, 700, 60, {"text": "Start here", "size": 40, "bold": True}))
    rows.append(st.new_item(bid, "frame", -720, -320, 1450, 840, {"title": "Your first board", "color": "#9aa0ad"}))
    for r in rows:
        st.put_item(r)
    conns = [st.new_conn(bid, e1, e2, "leads to", {"color": "#d7263d"}),
             st.new_conn(bid, rows[0]["id"], e1, "", {"color": "#d7263d", "style": "dashed"}),
             st.new_conn(bid, e2, rows[3]["id"], "see it all", {"color": "#2f7cf6", "dir": "fwd"})]
    # a small sample script, attached like any file you drop on a board
    sample_dir = st.root / "samples"
    sample_dir.mkdir(exist_ok=True)
    sample = sample_dir / "peak_tracker_demo.py"
    sample.write_text(
        '"""Demo: track how a diffraction peak grows with temperature."""\n'
        "import numpy as np\n\n\n"
        "def gaussian(x, a, mu, sigma):\n"
        "    return a * np.exp(-0.5 * ((x - mu) / sigma) ** 2)\n\n\n"
        "temps = np.arange(300, 801, 50)\n"
        "areas = [gaussian(t, 1.0, 650, 90) for t in temps]  # peak area vs T\n"
        "for t, a in zip(temps, areas):\n"
        "    print(f\"{t:4d} K  area={a:.3f}\")\n", encoding="utf-8")
    fid = evidence.attach_file(bid, QPointF(185, -80), sample)
    conns.append(st.new_conn(bid, fid, e2, "generated by", {"color": "#12a594"}))
    for c in conns:
        st.put_conn(c)
    return b["id"]
