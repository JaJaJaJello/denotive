"""The library: one SQLite database plus a content-addressed asset folder.

projects -> boards -> items (evidence, files, notes, shapes, frames, lines) -> versions
connections are the strings between items. Everything searchable is mirrored into an FTS5 (trigram) table.
Deletions are soft (a `deleted` timestamp) so undo and the trash can bring things back.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT, sort REAL DEFAULT 0,
    created REAL, updated REAL, deleted REAL);
CREATE TABLE IF NOT EXISTS boards(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, sort REAL DEFAULT 0,
    background TEXT DEFAULT 'midnight', view TEXT, data TEXT,
    created REAL, updated REAL, deleted REAL, kind TEXT DEFAULT 'board');
CREATE INDEX IF NOT EXISTS boards_project ON boards(project_id);
CREATE TABLE IF NOT EXISTS items(
    id TEXT PRIMARY KEY, board_id TEXT NOT NULL, kind TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL, z REAL, data TEXT, title TEXT, ver TEXT, vnum INTEGER,
    created REAL, updated REAL, deleted REAL, link_of TEXT);
CREATE INDEX IF NOT EXISTS items_board ON items(board_id);
CREATE INDEX IF NOT EXISTS items_updated ON items(updated);
CREATE TABLE IF NOT EXISTS versions(
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL, num INTEGER, title TEXT, doc TEXT, text TEXT,
    thumb TEXT, note TEXT, created REAL);
CREATE INDEX IF NOT EXISTS versions_item ON versions(item_id, num);
CREATE TABLE IF NOT EXISTS connections(
    id TEXT PRIMARY KEY, board_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
    label TEXT, data TEXT, created REAL, updated REAL, deleted REAL);
CREATE INDEX IF NOT EXISTS conn_board ON connections(board_id);
CREATE TABLE IF NOT EXISTS assets(hash TEXT PRIMARY KEY, ext TEXT, size INTEGER, name TEXT, created REAL);
CREATE TABLE IF NOT EXISTS chats(scope TEXT PRIMARY KEY, messages TEXT, updated REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    title, body, kind UNINDEXED, ref UNINDEXED, item UNINDEXED, board UNINDEXED, ver UNINDEXED,
    tokenize='trigram');
"""

ITEM_COLS = ("id", "board_id", "kind", "x", "y", "w", "h", "z", "data", "title", "ver", "vnum",
             "created", "updated", "deleted", "link_of")
CONN_COLS = ("id", "board_id", "src", "dst", "label", "data", "created", "updated", "deleted")
TEXT_KINDS = ("note", "sticky", "shape", "frame")
VERSIONED = ("evidence", "file")


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


def _like_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def text_title(text: str, limit: int = 80) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.root / "assets"
        self.assets_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "denotive.db"
        self.db = sqlite3.connect(str(self.db_path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "temp_store=MEMORY", "cache_size=-32000",
                       "foreign_keys=OFF", "busy_timeout=5000"):  # a second window waits its turn to write
            self.db.execute("PRAGMA " + pragma)
        self.db.executescript(SCHEMA)
        self._migrate()
        self._depth = 0
        self._asset_ext = {}

    def _migrate(self):
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(boards)")}
        if "kind" not in cols:
            # 'map' boards are the per-project canvas of boards
            self.db.execute("ALTER TABLE boards ADD COLUMN kind TEXT DEFAULT 'board'")
        if "link_of" not in {r[1] for r in self.db.execute("PRAGMA table_info(items)")}:
            # a linked copy shows another item's evidence/file and shares its versions
            self.db.execute("ALTER TABLE items ADD COLUMN link_of TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS items_link ON items(link_of)")

    def data_version(self):
        """Changes whenever another connection (another DeNotive window) commits to the library."""
        return self.db.execute("PRAGMA data_version").fetchone()[0]

    def board_fingerprint(self, bid):
        """Changes when anything on the board does (items, strings, the board itself)."""
        items = self.q("SELECT id, x, y, w, h, z, data, title, ver, vnum, deleted, link_of FROM items "
                       "WHERE board_id=? ORDER BY id", (bid,))
        conns = self.q("SELECT id, src, dst, label, data, deleted FROM connections WHERE board_id=? ORDER BY id", (bid,))
        b = self.q1("SELECT name, background, data, deleted FROM boards WHERE id=?", (bid,))
        return hash((tuple(map(tuple, items)), tuple(map(tuple, conns)), tuple(b) if b else None))

    def library_fingerprint(self):
        """Changes when projects or boards are added, renamed, moved, recoloured or deleted."""
        p = self.q("SELECT id, name, color, sort, deleted FROM projects ORDER BY id")
        b = self.q("SELECT id, project_id, name, sort, deleted, kind FROM boards ORDER BY id")
        return hash((tuple(map(tuple, p)), tuple(map(tuple, b))))

    def close(self):
        try:
            self.db.execute("PRAGMA optimize")
            self.db.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ infrastructure
    @contextmanager
    def tx(self):
        if self._depth == 0:
            self.db.execute("BEGIN")
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.db.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.db.execute("COMMIT")

    def q(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def q1(self, sql, params=()):
        return self.db.execute(sql, params).fetchone()

    def meta(self, key, default=None):
        r = self.q1("SELECT value FROM meta WHERE key=?", (key,))
        return r[0] if r else default

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))

    def _fts_put(self, kind, ref, item="", board="", ver=None, title="", body=""):
        self.db.execute("DELETE FROM fts WHERE ref=?", (ref,))
        self.db.execute("INSERT INTO fts(title, body, kind, ref, item, board, ver) VALUES(?,?,?,?,?,?,?)",
                        (title or "", body or "", kind, ref, item or "", board or "", ver))

    def _fts_del(self, ref):
        self.db.execute("DELETE FROM fts WHERE ref=?", (ref,))

    # ------------------------------------------------------------------ projects
    def projects(self):
        return [dict(r) for r in self.q("SELECT * FROM projects WHERE deleted IS NULL ORDER BY sort, created")]

    def project(self, pid):
        r = self.q1("SELECT * FROM projects WHERE id=?", (pid,))
        return dict(r) if r else None

    def create_project(self, name, color=None):
        from .theme import PROJECT_COLORS
        n = self.q1("SELECT COUNT(*), COALESCE(MAX(sort), 0) FROM projects")
        t = now()
        row = dict(id=new_id(), name=name, color=color or PROJECT_COLORS[n[0] % len(PROJECT_COLORS)],
                   sort=n[1] + 1, created=t, updated=t, deleted=None)
        with self.tx():
            self.db.execute("INSERT INTO projects(id,name,color,sort,created,updated,deleted) VALUES(?,?,?,?,?,?,?)",
                            tuple(row.values()))
            self._fts_put("project", row["id"], title=name)
        return row

    def update_project(self, pid, **fields):
        if not fields:
            return
        fields["updated"] = now()
        with self.tx():
            self.db.execute(f"UPDATE projects SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                            (*fields.values(), pid))
            if "name" in fields:
                self._fts_put("project", pid, title=fields["name"])

    def delete_project(self, pid):
        self.db.execute("UPDATE projects SET deleted=? WHERE id=?", (now(), pid))

    def restore_project(self, pid):
        self.db.execute("UPDATE projects SET deleted=NULL WHERE id=?", (pid,))

    # ------------------------------------------------------------------ boards
    def _board(self, r):
        d = dict(r)
        d["view"] = json.loads(d["view"]) if d.get("view") else None
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
        return d

    def boards(self, project_id=None, include_maps=False):
        maps = "" if include_maps else " AND COALESCE(kind, 'board') != 'map'"
        if project_id:
            rows = self.q(f"SELECT * FROM boards WHERE deleted IS NULL AND project_id=?{maps} ORDER BY sort, created",
                          (project_id,))
        else:
            rows = self.q(f"SELECT * FROM boards WHERE deleted IS NULL{maps} ORDER BY sort, created")
        return [self._board(r) for r in rows]

    @staticmethod
    def is_map(board):
        return bool(board) and board.get("kind") == "map"

    def board(self, bid):
        r = self.q1("SELECT * FROM boards WHERE id=?", (bid,))
        return self._board(r) if r else None

    def create_board(self, project_id, name, background="midnight", kind="board"):
        n = self.q1("SELECT COALESCE(MAX(sort), 0) FROM boards WHERE project_id=?", (project_id,))[0]
        t = now()
        row = dict(id=new_id(), project_id=project_id, name=name, sort=n + 1, background=background,
                   view=None, data=None, created=t, updated=t, deleted=None, kind=kind)
        with self.tx():
            self.db.execute("INSERT INTO boards(id,project_id,name,sort,background,view,data,created,updated,deleted,"
                            "kind) VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
            if kind != "map":
                self._fts_put("board", row["id"], board=row["id"], title=name)
        row["view"], row["data"] = None, {}
        return row

    # ------------------------------------------------------------------ project maps
    def project_map(self, project_id):
        """The project's canvas of boards (created on first use)."""
        r = self.q1("SELECT * FROM boards WHERE project_id=? AND kind='map' AND deleted IS NULL", (project_id,))
        if r:
            return self._board(r)
        return self.create_board(project_id, "Project map", "slate", kind="map")

    def sync_project_map(self, map_id):
        """Makes sure every board in the project has exactly one live card on its map."""
        m = self.board(map_id)
        if not self.is_map(m):
            return
        boards = self.boards(m["project_id"])
        live = {b["id"] for b in boards}
        cards = [self._item(r) for r in self.q("SELECT * FROM items WHERE board_id=? AND kind='boardlink' "
                                                "ORDER BY deleted IS NOT NULL, deleted DESC", (map_id,))]
        by_board = {}
        for c in cards:
            by_board.setdefault(c["data"].get("board_id"), []).append(c)
        t = now()
        with self.tx():
            for bid, cs in by_board.items():
                alive = [c for c in cs if not c["deleted"]]
                if bid not in live:
                    for c in alive:
                        self._soft_delete_item(c["id"], t)
                elif not alive:
                    self.restore_item(cs[0]["id"])
            existing = [c for c in cards if not c["deleted"] and c["data"].get("board_id") in live]
            bottom = max((c["y"] + c["h"] for c in existing), default=None)
            left = min((c["x"] for c in existing), default=0.0)
            k = 0
            for b in boards:
                if b["id"] in by_board:
                    continue
                if bottom is None:
                    x, y = k % 4 * 400.0, k // 4 * 320.0
                else:
                    x, y = left + k % 4 * 400.0, bottom + 90 + k // 4 * 320.0
                self.put_item(self.new_item(map_id, "boardlink", x, y, 320, 230, {"board_id": b["id"]}))
                k += 1

    def _soft_delete_item(self, iid, t):
        self.db.execute("UPDATE items SET deleted=? WHERE id=?", (t, iid))
        for c in self.q("SELECT id FROM connections WHERE (src=? OR dst=?) AND deleted IS NULL", (iid, iid)):
            self.db.execute("UPDATE connections SET deleted=? WHERE id=?", (t, c[0]))
            self._fts_del(c[0])

    def board_info(self, bid):
        """Summary used by board link cards: counts, last activity and the latest evidence thumbnails."""
        b = self.board(bid)
        if b is None:
            return None
        p = self.project(b["project_id"])
        counts = {r[0]: r[1] for r in self.q("SELECT kind, COUNT(*) FROM items WHERE board_id=? AND deleted IS NULL "
                                             "GROUP BY kind", (bid,))}
        strings = self.q1("SELECT COUNT(*) FROM connections WHERE board_id=? AND deleted IS NULL", (bid,))[0]
        last = self.q1("SELECT MAX(updated) FROM items WHERE board_id=? AND deleted IS NULL", (bid,))[0]
        thumbs = [r[0] for r in self.q("SELECT v.thumb FROM items i JOIN versions v ON v.id=i.ver "
                                       "WHERE i.board_id=? AND i.deleted IS NULL AND i.kind='evidence' "
                                       "ORDER BY i.updated DESC LIMIT 4", (bid,)) if r[0]]
        gone = bool(b["deleted"]) or p is None or bool(p["deleted"])
        return dict(board=b, project=p, evidence=counts.get("evidence", 0), files=counts.get("file", 0),
                    strings=strings, last=last or b["updated"], thumbs=thumbs, gone=gone)

    def update_board(self, bid, touch=True, **fields):
        if not fields:
            return
        for k in ("view", "data"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        if touch:
            fields["updated"] = now()
        with self.tx():
            self.db.execute(f"UPDATE boards SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                            (*fields.values(), bid))
            if "name" in fields:
                self._fts_put("board", bid, board=bid, title=fields["name"])

    def delete_board(self, bid):
        self.db.execute("UPDATE boards SET deleted=? WHERE id=?", (now(), bid))

    def restore_board(self, bid):
        self.db.execute("UPDATE boards SET deleted=NULL WHERE id=?", (bid,))

    def board_counts(self):
        rows = self.q("SELECT board_id, COUNT(*) FROM items WHERE deleted IS NULL AND kind IN ('evidence','file') "
                      "GROUP BY board_id")
        return {r[0]: r[1] for r in rows}

    def recent_boards(self, limit=12):
        rows = self.q("""
            SELECT b.*, p.name AS project_name, p.color AS project_color,
                   (SELECT COUNT(*) FROM items i WHERE i.board_id=b.id AND i.deleted IS NULL
                        AND i.kind IN ('evidence','file')) AS n,
                   (SELECT MAX(i.updated) FROM items i WHERE i.board_id=b.id AND i.deleted IS NULL) AS last
            FROM boards b JOIN projects p ON p.id=b.project_id
            WHERE b.deleted IS NULL AND p.deleted IS NULL AND COALESCE(b.kind, 'board') != 'map'
            ORDER BY MAX(COALESCE(last, 0), b.updated) DESC LIMIT ?""", (limit,))
        return [self._board(r) | {k: r[k] for k in ("project_name", "project_color", "n", "last")} for r in rows]

    # ------------------------------------------------------------------ items
    @staticmethod
    def _item(r):
        d = dict(r)
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
        if "vdoc" in d:
            d["vdoc"] = json.loads(d["vdoc"]) if d["vdoc"] else {}
        return d

    # board rows carry the current version's thumbnail (and, for files, its small doc) for drawing
    _ITEM_SELECT = ("SELECT i.*, v.thumb AS thumb, CASE WHEN i.kind='file' THEN v.doc END AS vdoc, "
                    "(SELECT COUNT(*) FROM items f WHERE f.deleted IS NULL AND "
                    " (f.id = COALESCE(i.link_of, i.id) OR f.link_of = COALESCE(i.link_of, i.id))) AS family "
                    "FROM items i LEFT JOIN versions v ON v.id = i.ver ")

    def items(self, board_id):
        return [self._item(r) for r in
                self.q(self._ITEM_SELECT + "WHERE i.board_id=? AND i.deleted IS NULL ORDER BY i.z", (board_id,))]

    def item(self, iid):
        r = self.q1(self._ITEM_SELECT + "WHERE i.id=?", (iid,))
        return self._item(r) if r else None

    def max_z(self, board_id):
        r = self.q1("SELECT MAX(z) FROM items WHERE board_id=? AND kind != 'frame'", (board_id,))
        return (r[0] or 0) if r else 0

    def new_item(self, board_id, kind, x, y, w, h, data=None, z=None, title=""):
        t = now()
        if z is None:
            if kind == "frame":
                r = self.q1("SELECT MIN(z) FROM items WHERE board_id=? AND kind='frame'", (board_id,))
                z = min(-100000.0, (r[0] if r and r[0] is not None else -99999.0) - 1)
            else:
                z = self.max_z(board_id) + 1
        return dict(id=new_id(), board_id=board_id, kind=kind, x=x, y=y, w=w, h=h, z=z, data=data or {},
                    title=title, ver=None, vnum=0, created=t, updated=t, deleted=None)

    def put_item(self, row):
        r = {k: row.get(k) for k in ITEM_COLS}
        data = r["data"] or {}
        if r["kind"] in TEXT_KINDS:
            r["title"] = data.get("title") if r["kind"] == "frame" else text_title(data.get("text", ""))
        r["data"] = json.dumps(data)
        # version bookkeeping (current version, title, content date) is owned by add_version()
        frozen = {"id", "created", "ver", "vnum", "link_of"} | ({"title", "updated"} if r["kind"] in VERSIONED else set())
        sets = ", ".join(f"{k}=excluded.{k}" for k in ITEM_COLS if k not in frozen)
        with self.tx():
            self.db.execute(f"INSERT INTO items({','.join(ITEM_COLS)}) VALUES({','.join('?' * len(ITEM_COLS))}) "
                            f"ON CONFLICT(id) DO UPDATE SET {sets}", tuple(r.values()))
            if r["kind"] in TEXT_KINDS:
                body = data.get("text", "")
                if body or r["title"]:
                    self._fts_put("note", r["id"], r["id"], r["board_id"], None, r["title"] or "", body)
                else:
                    self._fts_del(r["id"])

    def move_item_to_board(self, iid, board_id):
        with self.tx():
            self.db.execute("UPDATE items SET board_id=? WHERE id=?", (board_id, iid))
            self.db.execute("UPDATE fts SET board=? WHERE item=?", (board_id, iid))

    # ------------------------------------------------------------------ versions
    @staticmethod
    def _version(r):
        d = dict(r)
        d["doc"] = json.loads(d["doc"]) if d.get("doc") else {}
        return d

    def owner_of(self, item_id):
        """The item whose versions this one shows: itself, or the original of a linked copy."""
        r = self.q1("SELECT link_of FROM items WHERE id=?", (item_id,))
        return (r["link_of"] if r else None) or item_id

    def family_ids(self, item_id):
        """The original and all its linked copies (deleted ones too, so they stay in step for a restore)."""
        owner = self.owner_of(item_id)
        return [r[0] for r in self.q("SELECT id FROM items WHERE id=? OR link_of=?", (owner, owner))]

    def linked_places(self, item_id):
        """Where else the same evidence/file is pinned: [{id, board_id, board, project}], live boards only."""
        owner = self.owner_of(item_id)
        return [dict(r) for r in self.q(
            """SELECT i.id, i.board_id, b.name AS board, p.name AS project FROM items i
               JOIN boards b ON b.id=i.board_id JOIN projects p ON p.id=b.project_id
               WHERE (i.id=? OR i.link_of=?) AND i.id != ? AND i.deleted IS NULL
                 AND b.deleted IS NULL AND p.deleted IS NULL ORDER BY p.name, b.name""", (owner, owner, item_id))]

    def linked_copy_row(self, item_id, board_id, x, y):
        """A new row showing the same evidence/file as item_id; editing either edits both."""
        src = self.item(item_id)
        owner = self.owner_of(item_id)
        t = now()
        return dict(id=new_id(), board_id=board_id, kind=src["kind"], x=x, y=y, w=src["w"], h=src["h"],
                    z=self.max_z(board_id) + 1, data=dict(src["data"]), title=src["title"], ver=src["ver"],
                    vnum=src["vnum"], created=t, updated=src["updated"], deleted=None, link_of=owner)

    def _clone_versions(self, src, dst):
        """Gives dst its own copies of src's versions; returns {old version id: new id}."""
        board = (self.q1("SELECT board_id, kind FROM items WHERE id=?", (dst,)) or {})
        ids = {}
        for v in self.q("SELECT * FROM versions WHERE item_id=? ORDER BY num", (src,)):
            nid = new_id()
            ids[v["id"]] = nid
            self.db.execute("INSERT INTO versions(id,item_id,num,title,doc,text,thumb,note,created) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (nid, dst, v["num"], v["title"], v["doc"], v["text"], v["thumb"], v["note"], v["created"]))
            if board:
                self._fts_put(board["kind"], nid, dst, board["board_id"], v["num"], v["title"] or "", v["text"] or "")
        return ids

    def unlink(self, item_id):
        """Makes item_id independent of its linked copies: from now on its edits are its own."""
        owner = self.owner_of(item_id)
        copies = [r[0] for r in self.q("SELECT id FROM items WHERE link_of=?", (owner,))]
        with self.tx():
            if item_id != owner:
                ids = self._clone_versions(owner, item_id)
                ver = self.q1("SELECT ver FROM items WHERE id=?", (item_id,))["ver"]
                self.db.execute("UPDATE items SET link_of=NULL, ver=? WHERE id=?", (ids.get(ver, ver), item_id))
            elif copies:
                # the original goes its own way; the copies keep sharing with each other
                new_owner, rest = copies[0], copies[1:]
                ids = self._clone_versions(owner, new_owner)
                ver = self.q1("SELECT ver FROM items WHERE id=?", (new_owner,))["ver"]
                self.db.execute("UPDATE items SET link_of=NULL, ver=? WHERE id=?", (ids.get(ver, ver), new_owner))
                for c in rest:
                    self.db.execute("UPDATE items SET link_of=?, ver=? WHERE id=?", (new_owner, ids.get(ver, ver), c))

    def versions(self, item_id):
        return [self._version(r) for r in self.q("SELECT * FROM versions WHERE item_id=? ORDER BY num",
                                                 (self.owner_of(item_id),))]

    def version(self, vid):
        r = self.q1("SELECT * FROM versions WHERE id=?", (vid,))
        return self._version(r) if r else None

    def current_version(self, item_id):
        r = self.q1("SELECT v.* FROM versions v JOIN items i ON i.ver=v.id WHERE i.id=?", (item_id,))
        return self._version(r) if r else None

    def add_version(self, item_id, title, doc, text, thumb=None, note=None, ts=None):
        ts = ts or now()
        item_id = self.owner_of(item_id)  # a linked copy saves into the original, and every copy follows
        it = self.q1("SELECT kind, board_id FROM items WHERE id=?", (item_id,))
        with self.tx():
            num = (self.q1("SELECT COALESCE(MAX(num), 0) FROM versions WHERE item_id=?", (item_id,))[0] or 0) + 1
            vid = new_id()
            self.db.execute("INSERT INTO versions(id,item_id,num,title,doc,text,thumb,note,created) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (vid, item_id, num, title, json.dumps(doc), text, thumb, note, ts))
            self.db.execute("UPDATE items SET ver=?, vnum=?, title=?, updated=? WHERE id=? OR link_of=?",
                            (vid, num, title, ts, item_id, item_id))
            if it:
                self._fts_put(it["kind"], vid, item_id, it["board_id"], num, title, text)
        return self.version(vid)

    def overwrite_version(self, vid, title, doc, text, thumb=None):
        v = self.q1("SELECT item_id, num FROM versions WHERE id=?", (vid,))
        if not v:
            return None
        it = self.q1("SELECT kind, board_id, ver FROM items WHERE id=?", (v["item_id"],))
        t = now()
        with self.tx():
            self.db.execute("UPDATE versions SET title=?, doc=?, text=?, thumb=? WHERE id=?",
                            (title, json.dumps(doc), text, thumb, vid))
            if it and it["ver"] == vid:
                self.db.execute("UPDATE items SET title=?, updated=? WHERE id=? OR link_of=?",
                                (title, t, v["item_id"], v["item_id"]))
            if it:
                self._fts_put(it["kind"], vid, v["item_id"], it["board_id"], v["num"], title, text)
        return self.version(vid)

    # ------------------------------------------------------------------ connections
    @staticmethod
    def _conn(r):
        d = dict(r)
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
        return d

    def connections(self, board_id):
        return [self._conn(r) for r in
                self.q("SELECT * FROM connections WHERE board_id=? AND deleted IS NULL", (board_id,))]

    def item_connections(self, item_id):
        return [self._conn(r) for r in self.q(
            "SELECT * FROM connections WHERE (src=? OR dst=?) AND deleted IS NULL", (item_id, item_id))]

    def conn(self, cid):
        r = self.q1("SELECT * FROM connections WHERE id=?", (cid,))
        return self._conn(r) if r else None

    def new_conn(self, board_id, src, dst, label="", data=None):
        t = now()
        return dict(id=new_id(), board_id=board_id, src=src, dst=dst, label=label, data=data or {},
                    created=t, updated=t, deleted=None)

    def put_conn(self, row):
        r = {k: row.get(k) for k in CONN_COLS}
        r["data"] = json.dumps(r["data"] or {})
        sets = ", ".join(f"{k}=excluded.{k}" for k in CONN_COLS if k != "id")
        with self.tx():
            self.db.execute(f"INSERT INTO connections({','.join(CONN_COLS)}) VALUES({','.join('?' * len(CONN_COLS))}) "
                            f"ON CONFLICT(id) DO UPDATE SET {sets}", tuple(r.values()))
            if r["label"] and not r["deleted"]:
                self._fts_put("string", r["id"], r["src"], r["board_id"], None, "", r["label"])
            else:
                self._fts_del(r["id"])

    # ------------------------------------------------------------------ assets
    def _asset_file(self, h, ext):
        return self.assets_dir / h[:2] / (h + ("." + ext if ext else ""))

    def import_file(self, path, name=None):
        path = Path(path)
        sha = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        h = sha.hexdigest()
        ext = path.suffix.lower().lstrip(".")
        dest = self._asset_file(h, ext)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        self.db.execute("INSERT OR IGNORE INTO assets(hash, ext, size, name, created) VALUES(?,?,?,?,?)",
                        (h, ext, dest.stat().st_size, name or path.name, now()))
        self._asset_ext[h] = ext
        return h

    def import_bytes(self, data: bytes, ext: str, name=""):
        h = hashlib.sha1(data).hexdigest()
        dest = self._asset_file(h, ext)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        self.db.execute("INSERT OR IGNORE INTO assets(hash, ext, size, name, created) VALUES(?,?,?,?,?)",
                        (h, ext, len(data), name, now()))
        self._asset_ext[h] = ext
        return h

    def asset_path(self, h):
        if not h:
            return None
        ext = self._asset_ext.get(h)
        if ext is None:
            r = self.q1("SELECT ext FROM assets WHERE hash=?", (h,))
            if not r:
                return None
            ext = self._asset_ext[h] = r[0] or ""
        return self._asset_file(h, ext)

    def asset_info(self, h):
        r = self.q1("SELECT * FROM assets WHERE hash=?", (h,))
        return dict(r) if r else None

    # ------------------------------------------------------------------ search
    def search(self, text, scope=("all", None), kinds=None, include_old=True, limit=400):
        terms = [t.strip('"') for t in re.findall(r'"[^"]+"|\S+', text or "")]
        terms = [t for t in terms if t]
        if not terms:
            return []
        long_t = [t for t in terms if len(t) >= 3]
        short_t = [t for t in terms if len(t) < 3]
        where, params, sel_params = [], [], []
        if long_t:
            where.append("fts MATCH ?")
            params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_t))
            snip = "snippet(fts, 1, char(1), char(2), '…', 48)"
            order = "bm25(fts, 5.0, 1.0)"
        else:
            snip = "substr(fts.body, max(1, instr(lower(fts.body), lower(?)) - 60), 170)"
            sel_params.append(short_t[0])
            order = "COALESCE(i.updated, b.updated, 0) DESC"
        for t in short_t:
            pat = "%" + _like_escape(t) + "%"
            where.append("(fts.title LIKE ? ESCAPE '\\' OR fts.body LIKE ? ESCAPE '\\')")
            params += [pat, pat]
        skind, sid = scope
        if skind == "project":
            where.append("p.id = ?")
            params.append(sid)
        elif skind == "board":
            where.append("fts.board = ?")
            params.append(sid)
        elif skind == "item":
            where.append("fts.item = ?")
            params.append(sid)
        if kinds:
            where.append(f"fts.kind IN ({','.join('?' * len(kinds))})")
            params += list(kinds)
        if not include_old:
            where.append("(fts.kind NOT IN ('evidence','file') OR fts.ref = i.ver)")
        where += ["(i.id IS NULL OR i.deleted IS NULL)", "(b.id IS NULL OR b.deleted IS NULL)",
                  "(p.id IS NULL OR p.deleted IS NULL)"]
        sql = f"""
            SELECT fts.kind AS kind, fts.ref AS ref, fts.item AS item, fts.board AS board, fts.ver AS ver,
                   fts.title AS ftitle, {snip} AS snip,
                   i.kind AS ikind, i.ver AS cur_ver, i.title AS ititle, i.updated AS updated, i.created AS created,
                   b.name AS board_name, p.id AS project_id, p.name AS project_name, p.color AS project_color
            FROM fts
            LEFT JOIN items i ON i.id = fts.item AND fts.item != ''
            LEFT JOIN boards b ON b.id = fts.board AND fts.board != ''
            LEFT JOIN projects p ON p.id = (CASE WHEN fts.kind = 'project' THEN fts.ref ELSE b.project_id END)
            WHERE {' AND '.join(where)}
            ORDER BY {order}
            LIMIT ?"""
        try:
            rows = self.q(sql, (*sel_params, *params, limit))
        except sqlite3.OperationalError:
            return []
        results, by_item = [], {}
        for r in rows:
            d = dict(r)
            if d["kind"] == "string" and d["ikind"] is None:
                continue
            if d["kind"] in VERSIONED and d["item"]:
                prev = by_item.get(d["item"])
                d["old"] = d["ref"] != d["cur_ver"]
                if prev is None:
                    d["hits"] = 1
                    by_item[d["item"]] = d
                    results.append(d)
                else:
                    prev["hits"] += 1
                    if prev["old"] and not d["old"]:
                        d["hits"] = prev["hits"]
                        results[results.index(prev)] = d
                        by_item[d["item"]] = d
                continue
            d["old"], d["hits"] = False, 1
            results.append(d)
        low = [t.lower() for t in terms]
        for d in results:
            if not long_t:
                d["snip"] = _mark(d["snip"] or "", low)
            d["title"] = d["ititle"] if d["kind"] in VERSIONED else (d["ftitle"] or d["ititle"] or "")
            if d["kind"] in VERSIONED and d["old"]:
                d["title"] = d["ftitle"] or d["ititle"]
            d["title_marked"] = _mark(d["title"] or "", low)
        return results

    # ------------------------------------------------------------------ timeline feed
    def feed(self, project_id=None, board_id=None, kinds=("evidence",), sort="updated", since=None, limit=5000):
        sort_col = "i.created" if sort == "created" else "i.updated"
        where = ["i.deleted IS NULL", "b.deleted IS NULL", "p.deleted IS NULL",
                 f"i.kind IN ({','.join('?' * len(kinds))})"]
        params = list(kinds)
        if project_id:
            where.append("p.id = ?")
            params.append(project_id)
        if board_id:
            where.append("b.id = ?")
            params.append(board_id)
        if since:
            where.append(f"{sort_col} >= ?")
            params.append(since)
        rows = self.q(f"""
            SELECT i.id, i.kind, i.title, i.created, i.updated, i.board_id, i.vnum, i.data, i.link_of,
                   b.name AS board_name, p.id AS project_id, p.name AS project_name, p.color AS project_color,
                   v.thumb AS thumb, substr(v.text, 1, 600) AS text
            FROM items i JOIN boards b ON b.id = i.board_id JOIN projects p ON p.id = b.project_id
            LEFT JOIN versions v ON v.id = i.ver
            WHERE {' AND '.join(where)}
            ORDER BY {sort_col} DESC LIMIT ?""", (*params, limit))
        out, seen = [], set()
        for r in rows:
            d = dict(r)
            if not board_id:  # linked copies are one piece of evidence
                key = d.get("link_of") or d["id"]
                if key in seen:
                    continue
                seen.add(key)
            d["data"] = json.loads(d["data"]) if d["data"] else {}
            if d["kind"] in TEXT_KINDS:
                d["text"] = d["data"].get("text", "")
            out.append(d)
        return out

    # ------------------------------------------------------------------ trash
    def trash(self):
        out = []
        for r in self.q("SELECT id, name, deleted FROM projects WHERE deleted IS NOT NULL"):
            out.append(dict(type="project", id=r["id"], name=r["name"], where="", deleted=r["deleted"]))
        for r in self.q("""SELECT b.id, b.name, b.deleted, p.name AS pname FROM boards b
                           JOIN projects p ON p.id=b.project_id
                           WHERE b.deleted IS NOT NULL AND p.deleted IS NULL"""):
            out.append(dict(type="board", id=r["id"], name=r["name"], where=r["pname"], deleted=r["deleted"]))
        for r in self.q("""SELECT i.id, i.kind, i.title, i.deleted, b.name AS bname, p.name AS pname FROM items i
                           JOIN boards b ON b.id=i.board_id JOIN projects p ON p.id=b.project_id
                           WHERE i.deleted IS NOT NULL AND b.deleted IS NULL AND p.deleted IS NULL
                             AND i.kind IN ('evidence','file','note','sticky','shape','frame')"""):
            out.append(dict(type=r["kind"], id=r["id"], name=r["title"] or "(untitled)",
                            where=f"{r['pname']} › {r['bname']}", deleted=r["deleted"]))
        out.sort(key=lambda d: -d["deleted"])
        return out

    def restore_item(self, iid):
        with self.tx():
            t = self.q1("SELECT deleted FROM items WHERE id=?", (iid,))
            self.db.execute("UPDATE items SET deleted=NULL WHERE id=?", (iid,))
            if t and t[0]:
                # bring back the strings that were cut when it was deleted
                self.db.execute("UPDATE connections SET deleted=NULL WHERE (src=? OR dst=?) AND deleted >= ? - 1",
                                (iid, iid, t[0]))

    def _purge_item(self, iid):
        copies = [r[0] for r in self.q("SELECT id FROM items WHERE link_of=?", (iid,))]
        if copies:  # the original is going for good: a copy takes over its versions
            heir = copies[0]
            board = self.q1("SELECT board_id FROM items WHERE id=?", (heir,))[0]
            self.db.execute("UPDATE versions SET item_id=? WHERE item_id=?", (heir, iid))
            self.db.execute("UPDATE fts SET item=?, board=? WHERE item=? AND kind IN ('evidence','file')",
                            (heir, board, iid))
            self.db.execute("UPDATE items SET link_of=NULL WHERE id=?", (heir,))
            self.db.execute("UPDATE items SET link_of=? WHERE link_of=?", (heir, iid))
        self.db.execute("DELETE FROM versions WHERE item_id=?", (iid,))
        self.db.execute("DELETE FROM fts WHERE item=?", (iid,))
        for c in self.q("SELECT id FROM connections WHERE src=? OR dst=?", (iid, iid)):
            self.db.execute("DELETE FROM fts WHERE ref=?", (c[0],))
        self.db.execute("DELETE FROM connections WHERE src=? OR dst=?", (iid, iid))
        self.db.execute("DELETE FROM items WHERE id=?", (iid,))

    def _purge_board(self, bid):
        for r in self.q("SELECT id FROM items WHERE board_id=?", (bid,)):
            self._purge_item(r[0])
        self.db.execute("DELETE FROM connections WHERE board_id=?", (bid,))
        self.db.execute("DELETE FROM fts WHERE board=? OR ref=?", (bid, bid))
        self.db.execute("DELETE FROM boards WHERE id=?", (bid,))

    def purge(self, kind, oid):
        with self.tx():
            if kind == "project":
                for r in self.q("SELECT id FROM boards WHERE project_id=?", (oid,)):
                    self._purge_board(r[0])
                self.db.execute("DELETE FROM fts WHERE ref=?", (oid,))
                self.db.execute("DELETE FROM projects WHERE id=?", (oid,))
            elif kind == "board":
                self._purge_board(oid)
            else:
                self._purge_item(oid)

    def empty_trash(self):
        with self.tx():
            for d in self.trash():
                self.purge("project" if d["type"] == "project" else "board" if d["type"] == "board" else "item",
                           d["id"])
            # line/connection leftovers that never show in the trash list
            for r in self.q("SELECT id FROM items WHERE deleted IS NOT NULL"):
                self._purge_item(r[0])
            self.db.execute("DELETE FROM connections WHERE deleted IS NOT NULL")
        self.gc_assets()

    def gc_assets(self):
        refs = set()
        hexre = re.compile(r"[0-9a-f]{40}")
        for r in self.q("SELECT doc, thumb FROM versions"):
            refs.update(hexre.findall(r[0] or ""))
            if r[1]:
                refs.add(r[1])
        for r in self.q("SELECT data FROM items"):
            refs.update(hexre.findall(r[0] or ""))
        removed = 0
        for r in self.q("SELECT hash FROM assets"):
            if r[0] not in refs:
                p = self.asset_path(r[0])
                try:
                    if p and p.exists():
                        p.unlink()
                except OSError:
                    continue
                self.db.execute("DELETE FROM assets WHERE hash=?", (r[0],))
                removed += 1
        return removed

    # ------------------------------------------------------------------ backups
    def backup_to(self, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            self.db.backup(target)
        finally:
            target.close()

    def daily_backup(self, keep=14):
        folder = self.root / "backups"
        name = f"denotive-{datetime.now():%Y-%m-%d}.db"
        if (folder / name).exists():
            return
        try:
            self.backup_to(folder / name)
            old = sorted(folder.glob("denotive-*.db"))
            for p in old[:-keep]:
                p.unlink()
        except (OSError, sqlite3.Error):
            pass

    # ------------------------------------------------------------------ Co-Investigator conversations
    def chat(self, scope):
        """Messages of the conversation kept for a scope such as 'board:<id>' or 'project:<id>'."""
        r = self.q1("SELECT messages FROM chats WHERE scope=?", (scope,))
        return json.loads(r[0]) if r and r[0] else []

    def save_chat(self, scope, messages):
        self.db.execute("INSERT INTO chats(scope, messages, updated) VALUES(?,?,?) "
                        "ON CONFLICT(scope) DO UPDATE SET messages=excluded.messages, updated=excluded.updated",
                        (scope, json.dumps(messages), now()))

    def stats(self):
        r = self.q1("""SELECT (SELECT COUNT(*) FROM projects WHERE deleted IS NULL),
                              (SELECT COUNT(*) FROM boards WHERE deleted IS NULL
                                   AND COALESCE(kind, 'board') != 'map'),
                              (SELECT COUNT(*) FROM items WHERE deleted IS NULL AND kind='evidence'),
                              (SELECT COUNT(*) FROM items WHERE deleted IS NULL AND kind='file'),
                              (SELECT COUNT(*) FROM connections WHERE deleted IS NULL)""")
        return dict(projects=r[0], boards=r[1], evidence=r[2], files=r[3], strings=r[4])


def _mark(text, terms):
    if not text:
        return text
    low = text.lower()
    spans = []
    for t in terms:
        start = 0
        while t and (i := low.find(t, start)) >= 0:
            spans.append((i, i + len(t)))
            start = i + len(t)
    if not spans:
        return text
    spans.sort()
    out, pos = [], 0
    for a, b in spans:
        if a < pos:
            continue
        out.append(text[pos:a] + "\x01" + text[a:b] + "\x02")
        pos = b
    out.append(text[pos:])
    return "".join(out)
