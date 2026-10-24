"""Pull searchable text (and a short preview) out of attached files without heavy dependencies."""
import json
import re
import zipfile
from pathlib import Path

TEXT_EXTS = {
    "txt", "md", "py", "pyw", "pyi", "m", "jl", "r", "c", "cc", "cpp", "h", "hpp", "cs", "java", "js", "ts",
    "tsx", "jsx", "rs", "go", "rb", "pl", "sh", "bat", "cmd", "ps1", "ini", "cfg", "conf", "toml", "yaml",
    "yml", "json", "xml", "html", "htm", "css", "csv", "tsv", "log", "tex", "bib", "sql", "xy", "xye", "chi",
    "qchi", "dat", "gr", "fq", "sq", "cif", "poni", "prm", "inp", "out", "spec", "rst", "f90", "f", "lua",
    "kt", "swift", "vb", "mac", "macro", "par", "lst", "dif", "pdb", "xyz",
}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}
MAX_INDEX = 2_000_000
_TAG = re.compile(r"<[^>]+>")


def ext_of(path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as ex:
        if ex.start >= len(raw) - 4:  # read cut a multi-byte character in half
            return raw.decode("utf-8-sig", errors="ignore")
        return raw.decode("cp1252", errors="replace")


def looks_like_text(path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if not head:
        return True
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    bad = sum(1 for b in head if b < 9 or 13 < b < 32)
    return bad / len(head) < 0.02


def is_text(path) -> bool:
    e = ext_of(path)
    if e in TEXT_EXTS:
        return True
    if e in IMAGE_EXTS or e in ("pdf", "docx", "pptx", "xlsx", "exe", "dll", "zip", "npy", "npz", "h5", "mat"):
        return False
    return looks_like_text(path)


def _zip_text(path, members_pattern, sep_tags):
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if re.fullmatch(members_pattern, n))
            for n in names:
                xml = z.read(n).decode("utf-8", errors="replace")
                for t in sep_tags:
                    xml = xml.replace(t, "\n")
                out.append(_TAG.sub("", xml))
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    text = "\n".join(out)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def extract(path) -> str:
    """Searchable text for a file ('' when not text-like)."""
    p = Path(path)
    e = ext_of(p)
    try:
        if e == "ipynb":
            nb = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            parts = []
            for cell in nb.get("cells", []):
                src = cell.get("source", "")
                parts.append("".join(src) if isinstance(src, list) else str(src))
            return "\n\n".join(parts)[:MAX_INDEX]
        if e == "docx":
            return _zip_text(p, r"word/document\.xml", ["</w:p>"])[:MAX_INDEX]
        if e == "pptx":
            return _zip_text(p, r"ppt/slides/slide\d+\.xml", ["</a:p>"])[:MAX_INDEX]
        if e == "xlsx":
            return _zip_text(p, r"xl/sharedStrings\.xml", ["</si>"])[:MAX_INDEX]
        if is_text(p):
            with open(p, "rb") as f:
                return _decode(f.read(MAX_INDEX))
    except (OSError, ValueError):
        pass
    return ""


def preview(text: str, lines: int = 18, width: int = 120) -> str:
    out = []
    for ln in text.splitlines():
        out.append(ln.expandtabs(4)[:width])
        if len(out) >= lines:
            break
    return "\n".join(out)
