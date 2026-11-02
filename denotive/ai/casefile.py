"""The case file: a plain-text (Markdown) description of a board or project that any model can read.

Every item gets a short handle (E3 = evidence, F1 = file, N2 = note, B1 = linked board) so answers can cite
items and the app can turn those citations into links back to the board.
"""
import base64
import re
from datetime import datetime

from .. import context, textmarks

KIND_PREFIX = {"evidence": "E", "file": "F", "note": "N", "sticky": "N", "shape": "N", "boardlink": "B"}
ARROWS = {"none": "—", "fwd": "→", "back": "←", "both": "↔"}
CAPS = [(2500, 2500, 60), (900, 900, 30), (300, 300, 12)]  # (slide text, notes, file preview lines) per pass
HANDLE_RE = re.compile(r"\b([EFNB]\d{1,4})\b")

READING_GUIDE = (
    "How to read this case file: E# = evidence (a slide the researcher built: plots, screenshots, text), "
    "F# = an attached file (scripts, data, documents; a stored snapshot of the exact version used), "
    "N# = a note, sticky note or labelled shape on the board, B# = a link to another board. "
    "Strings are connections the researcher drew between two items; the label says how they relate, "
    "“tentative” means a dashed string, and arrows show direction. Frames are labelled regions that group items. "
    "vN means the item has been revised N times (older versions are kept).")


def _when(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"


def _clip(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + " …[truncated]"


def _quote(text):
    return "\n".join("> " + ln if ln.strip() else ">" for ln in text.splitlines())


def _inside(outer, it):
    return (outer["x"] <= it["x"] and outer["y"] <= it["y"] and it["x"] + it["w"] <= outer["x"] + outer["w"]
            and it["y"] + it["h"] <= outer["y"] + outer["h"])


class CaseFile:
    def __init__(self):
        self.handles = {}       # handle -> item id
        self.by_item = {}       # item id -> handle
        self.board_of = {}      # item id -> board id
        self.counters = {}
        self.text = ""
        self.images = []        # [(mime, b64, caption)]

    def handle(self, it):
        if it["id"] in self.by_item:
            return self.by_item[it["id"]]
        p = KIND_PREFIX.get(it["kind"], "N")
        self.counters[p] = self.counters.get(p, 0) + 1
        h = f"{p}{self.counters[p]}"
        self.handles[h] = it["id"]
        self.by_item[it["id"]] = h
        self.board_of[it["id"]] = it["board_id"]
        return h

    def evidence_ids(self):
        return [iid for h, iid in self.handles.items() if h.startswith("E")]


def _board_section(cf, board, items, conns, caps, only=None, heading="#"):
    """Markdown for one board. `only` limits it to a set of item ids (a selection)."""
    st = context.store
    slide_cap, notes_cap, file_lines = caps
    from ..export import reading_order
    if only is not None:
        items = [i for i in items if i["id"] in only]
        conns = [c for c in conns if c["src"] in only or c["dst"] in only]
    by_id = {i["id"]: i for i in st.items(board["id"])}
    frames = [i for i in items if i["kind"] == "frame"]
    evidence = reading_order([i for i in items if i["kind"] == "evidence"])
    files = [i for i in items if i["kind"] == "file"]
    notes = [i for i in items if i["kind"] in ("note", "sticky", "shape") and (i["data"].get("text") or "").strip()]
    links = [i for i in items if i["kind"] == "boardlink"]
    for group in (evidence, files, notes, links):
        for it in group:
            cf.handle(it)

    def frame_of(it):
        names = [f["data"].get("title") or "Untitled frame" for f in frames if _inside(f, it)]
        return ", ".join(names)

    out = []
    p = st.project(board["project_id"])
    title = "Project map" if st.is_map(board) else board["name"]
    out.append(f"{heading} Board: {title}" + (f"  (project: {p['name']})" if p else ""))
    out.append(f"{len(evidence)} evidence · {len(files)} files · {len(notes)} notes · {len(conns)} strings"
               + (f" · {len(links)} linked boards" if links else ""))
    if frames:
        out.append(f"\n{heading}# Frames (sections of the board)")
        for f in frames:
            members = [cf.by_item[i["id"]] for i in evidence + files + notes + links if _inside(f, i)]
            out.append(f"- “{f['data'].get('title') or 'Untitled frame'}”: {', '.join(members) or '(empty)'}")
    if evidence:
        out.append(f"\n{heading}# Evidence")
        for it in evidence:
            v = st.current_version(it["id"])
            if v is None:
                continue
            doc = v["doc"] or {}
            notes_text = (doc.get("notes") or "").strip()
            body = (v.get("text") or "").strip()
            if any(textmarks.has_marks(d.get("html")) for d in doc.get("items", [])):
                # write formatting out so 10⁻³ doesn't read as "10-3", and crossed-out text doesn't read as a claim
                body = "\n".join(t for t in (textmarks.html_text(d.get("html")) for d in
                                             sorted(doc.get("items", []), key=lambda d: d.get("z", 0))
                                             if d.get("html") or d.get("plain")) if t.strip()) + \
                    ("\n" + notes_text if notes_text else "")
            if notes_text and body.endswith(notes_text):
                body = body[: -len(notes_text)].strip()
            line = f"\n{heading}## {cf.by_item[it['id']]} · {v['title'] or it.get('title') or 'Untitled'}"
            meta = f"v{v['num']} · created {_when(it['created'])} · updated {_when(it['updated'])}"
            fr = frame_of(it)
            if fr:
                meta += f" · in frame “{fr}”"
            imgs = [d for d in doc.get("items", []) if d.get("type") == "image"]
            if imgs:
                meta += f" · {len(imgs)} picture{'s' if len(imgs) != 1 else ''} on the slide"
            out += [line, meta]
            if body:
                out.append("Slide text:\n" + _quote(_clip(body, slide_cap)))
            if notes_text:
                out.append("Notes:\n" + _clip(notes_text, notes_cap))
            if v["num"] > 1:
                older = [o for o in st.versions(it["id"]) if o["id"] != v["id"]][-3:]
                out.append("Earlier versions: " + "; ".join(
                    f"v{o['num']} “{o['title']}” ({_when(o['created'])})" for o in older))
    if files:
        out.append(f"\n{heading}# Attached files")
        for it in files:
            d = it["data"]
            vdoc = it.get("vdoc") or {}
            out.append(f"\n{heading}## {cf.by_item[it['id']]} · {d.get('name', 'file')}")
            out.append(f"Path: {d.get('path', '?')} · v{it.get('vnum') or 1} · "
                       f"{'snapshot stored' if vdoc.get('asset') else 'linked only'}")
            prev = vdoc.get("preview") or ""
            if prev:
                lines = prev.splitlines()[:file_lines]
                ext = (d.get("ext") or "").lower()
                out.append(f"First lines:\n```{ext}\n" + "\n".join(lines) + "\n```")
    if notes:
        out.append(f"\n{heading}# Notes on the board")
        for it in notes:
            kind = {"sticky": "sticky note", "shape": "labelled shape", "note": "text"}[it["kind"]]
            who = " (written by the Co-Investigator)" if it["data"].get("author") == "coinv" else ""
            raw = textmarks.item_text(it["data"])
            text = " / ".join(ln.strip() for ln in _clip(raw, notes_cap).splitlines() if ln.strip())
            out.append(f"- {cf.by_item[it['id']]} ({kind}{who}): {text}")
    if links:
        out.append(f"\n{heading}# Linked boards")
        for it in links:
            info = st.board_info(it["data"].get("board_id")) or {}
            b = info.get("board") or {}
            pj = info.get("project") or {}
            out.append(f"- {cf.by_item[it['id']]} · board “{b.get('name', '?')}” in project “{pj.get('name', '?')}”: "
                       f"{info.get('evidence', 0)} evidence, {info.get('files', 0)} files")
    if conns:
        out.append(f"\n{heading}# Strings (connections drawn by the researcher)")
        for c in conns:
            a, b = by_id.get(c["src"]), by_id.get(c["dst"])
            if not a or not b:
                continue
            ha = cf.by_item.get(a["id"]) or _outside_name(a)
            hb = cf.by_item.get(b["id"]) or _outside_name(b)
            d = c.get("data") or {}
            arrow = ARROWS.get(d.get("dir", "none"), "—")
            label = f" “{c['label']}”" if c.get("label") else " (no label)"
            tent = " (tentative)" if d.get("style") == "dashed" else ""
            out.append(f"- {ha} {arrow} {hb}:{label}{tent}")
    return "\n".join(out)


def _outside_name(it):
    if it["kind"] == "frame":
        return f"frame “{it['data'].get('title') or 'Untitled'}”"
    return f"“{it.get('title') or it['data'].get('text', '')[:40] or it['kind']}” (outside this selection)"


def _attach_images(cf, prefer, max_images):
    st = context.store
    order = [i for i in prefer if i in cf.by_item] + [i for i in cf.evidence_ids() if i not in prefer]
    for iid in order[:max_images]:
        v = st.current_version(iid)
        p = st.asset_path(v.get("thumb")) if v else None
        if p and p.exists():
            cf.images.append(("image/jpeg", base64.b64encode(p.read_bytes()).decode(), cf.by_item[iid]))


def _finish(cf, header, sections, max_chars, images, prefer, max_images):
    cf.text = header + "\n\n" + READING_GUIDE + "\n\n" + "\n\n".join(sections)
    if images:
        _attach_images(cf, prefer, max_images)
        if cf.images:
            cf.text += ("\n\nImages attached, in order: " + ", ".join(f"image {n + 1} = slide {h}"
                                                                     for n, (_, _, h) in enumerate(cf.images)))
    return cf


def board_case(board_id, only=None, max_chars=60000, images=False, max_images=6, prefer=()):
    st = context.store
    b = st.board(board_id)
    items = st.items(board_id)
    conns = st.connections(board_id)
    for caps in CAPS:
        cf = CaseFile()
        sec = _board_section(cf, b, items, conns, caps, only)
        if len(sec) <= max_chars or caps is CAPS[-1]:
            break
    scope = "selected items on a board" if only else "one board"
    header = f"# DeNotive case file — {scope}\nExported {datetime.now():%Y-%m-%d %H:%M}"
    return _finish(cf, header, [sec], max_chars, images, list(prefer), max_images)


def project_case(project_id, max_chars=90000, images=False, max_images=6):
    st = context.store
    p = st.project(project_id)
    boards = st.boards(project_id)
    m = st.project_map(project_id)
    st.sync_project_map(m["id"])
    for caps in CAPS:
        cf = CaseFile()
        sections = []
        # the map: how the boards relate
        mitems = st.items(m["id"])
        cards = {i["id"]: i for i in mitems if i["kind"] == "boardlink"}
        names = {cid: (st.board(c["data"].get("board_id")) or {}).get("name", "?") for cid, c in cards.items()}
        rel = []
        for c in st.connections(m["id"]):
            if c["src"] in names and c["dst"] in names:
                arrow = ARROWS.get((c.get("data") or {}).get("dir", "none"), "—")
                rel.append(f"- “{names[c['src']]}” {arrow} “{names[c['dst']]}”"
                           + (f": “{c['label']}”" if c.get("label") else ""))
        sections.append("## How the boards relate (strings on the project map)\n" + ("\n".join(rel) or "(none yet)"))
        for b in boards:
            sections.append(_board_section(cf, b, st.items(b["id"]), st.connections(b["id"]), caps, heading="##"))
        if sum(len(s) for s in sections) <= max_chars or caps is CAPS[-1]:
            break
    header = (f"# DeNotive case file — project “{p['name']}”\n{len(boards)} boards · "
              f"exported {datetime.now():%Y-%m-%d %H:%M}")
    return _finish(cf, header, sections, max_chars, images, [], max_images)


def evidence_brief(title, slide_text, notes, item_id=None, board_id=None):
    """Context for drafting one piece of evidence's notes: the slide, its notes and what it's tied to."""
    st = context.store
    parts = [f"Evidence title: {title or '(not titled yet)'}"]
    if board_id:
        b = st.board(board_id)
        p = st.project(b["project_id"]) if b else None
        if b:
            parts.append(f"Board: {'Project map' if st.is_map(b) else b['name']}" + (f" (project {p['name']})" if p else ""))
    parts.append("Slide text:\n" + (_quote(_clip(slide_text, 4000)) if slide_text.strip() else "(no text on the slide)"))
    parts.append("Notes already written:\n" + (_clip(notes, 3000) if notes.strip() else "(none yet)"))
    if item_id:
        it = st.item(item_id)
        by_id = {i["id"]: i for i in st.items(it["board_id"])} if it else {}
        rel = []
        for c in st.item_connections(item_id):
            other = by_id.get(c["dst"] if c["src"] == item_id else c["src"])
            if not other:
                continue
            name = other.get("title") or other["data"].get("text", "")[:80] or other["kind"]
            if other["kind"] == "boardlink":
                name = "board “" + ((st.board(other["data"].get("board_id")) or {}).get("name", "?")) + "”"
            line = f"- {other['kind']} “{name}”" + (f" — string labelled “{c['label']}”" if c.get("label") else "")
            if other["kind"] == "file":
                prev = (other.get("vdoc") or {}).get("preview") or ""
                if prev:
                    line += "\n  first lines:\n" + "\n".join("    " + ln for ln in prev.splitlines()[:25])
            elif other["kind"] == "evidence":
                ov = st.current_version(other["id"])
                if ov and (ov["doc"] or {}).get("notes"):
                    line += f"\n  its notes: {_clip(ov['doc']['notes'], 400)}"
            rel.append(line)
        if rel:
            parts.append("Connected on the board:\n" + "\n".join(rel))
        vs = st.versions(item_id)
        if len(vs) > 1:
            parts.append(f"This is a revision; there are {len(vs)} saved versions.")
    return "\n\n".join(parts)


def save_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
